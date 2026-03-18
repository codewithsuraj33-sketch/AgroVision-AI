from __future__ import annotations

import uuid

from flask import abort, redirect, render_template, request, session


def register_admin_routes(app, deps: dict) -> None:
    db = deps["db"]
    AdminUser = deps["AdminUser"]
    StoreProduct = deps["StoreProduct"]
    StoreOrder = deps["StoreOrder"]
    DiseaseProductMapping = deps["DiseaseProductMapping"]

    ADMIN_EMAIL = deps["ADMIN_EMAIL"]
    STORE_CATEGORY_ORDER = deps["STORE_CATEGORY_ORDER"]
    FULFILLMENT_STATUS_ORDER = deps["FULFILLMENT_STATUS_ORDER"]

    is_admin_authenticated = deps["is_admin_authenticated"]
    admin_required = deps["admin_required"]
    require_csrf = deps["require_csrf"]
    rate_limit_exceeded = deps["rate_limit_exceeded"]
    _client_ip = deps["_client_ip"]
    check_admin_password = deps["check_admin_password"]
    get_fulfillment_status = deps["get_fulfillment_status"]
    set_fulfillment_status = deps["set_fulfillment_status"]
    normalize_disease_key = deps["normalize_disease_key"]
    slugify_crop_name = deps["slugify_crop_name"]
    estimate_store_mrp = deps["estimate_store_mrp"]
    compute_store_discount = deps["compute_store_discount"]
    save_product_image_upload = deps["save_product_image_upload"]
    default_store_seller = deps["default_store_seller"]

    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        if is_admin_authenticated():
            return redirect("/admin")

        if request.method == "POST":
            csrf_resp = require_csrf()
            if csrf_resp is not None:
                return render_template(
                    "admin/login.html",
                    error="Security check failed. Please refresh and try again.",
                    admin_email=ADMIN_EMAIL,
                )

            if rate_limit_exceeded(f"admin_login:{_client_ip()}", max_hits=10, window_seconds=10 * 60):
                return render_template(
                    "admin/login.html",
                    error="Too many attempts. Please wait and try again.",
                    admin_email=ADMIN_EMAIL,
                )

            email = (request.form.get("email") or "").strip().lower()
            password = (request.form.get("password") or "").strip()
            if email == ADMIN_EMAIL and check_admin_password(password):
                session["admin_authed"] = True
                session["admin_email"] = ADMIN_EMAIL
                try:
                    admin_row = AdminUser.query.filter_by(email=ADMIN_EMAIL).first()  # type: ignore
                    if admin_row is not None:
                        session["admin_id"] = int(admin_row.id)
                except Exception:
                    pass
                return redirect("/admin")

            return render_template("admin/login.html", error="Invalid admin credentials.", admin_email=ADMIN_EMAIL)

        return render_template("admin/login.html", error=None, admin_email=ADMIN_EMAIL)

    @app.route("/admin/logout", methods=["GET"])
    def admin_logout():
        session.pop("admin_authed", None)
        session.pop("admin_email", None)
        session.pop("admin_id", None)
        return redirect("/admin/login")

    @app.route("/admin", methods=["GET"])
    @admin_required
    def admin_dashboard():
        products = StoreProduct.query.all()
        paid_orders = StoreOrder.query.filter_by(status="paid").order_by(StoreOrder.created_at.desc()).all()

        total_products = len(products)
        total_orders = len(paid_orders)
        pending_orders = sum(1 for order in paid_orders if get_fulfillment_status(order) == "pending")
        revenue = sum(int(order.amount or 0) for order in paid_orders) / 100.0

        return render_template(
            "admin/dashboard.html",
            total_products=total_products,
            total_orders=total_orders,
            pending_orders=pending_orders,
            revenue=revenue,
        )

    @app.route("/admin/products", methods=["GET", "POST"])
    @admin_required
    def admin_products():
        error = None
        success = None

        if request.method == "POST":
            csrf_resp = require_csrf()
            if csrf_resp is not None:
                error = "Security check failed. Please refresh and try again."
            else:
                name = (request.form.get("name") or "").strip()
                category = (request.form.get("category") or "Organic").strip() or "Organic"
                image_url = (request.form.get("image_url") or "").strip()
                description = (request.form.get("description") or "").strip()
                is_active = request.form.get("is_active") == "on"
                image_file = request.files.get("image_file")

                try:
                    price = int(request.form.get("price") or 0)
                except (TypeError, ValueError):
                    price = 0

                try:
                    stock = int(request.form.get("stock") or 0)
                except (TypeError, ValueError):
                    stock = 0

                if not name or price <= 0:
                    error = "Product name and price are required."
                else:
                    slug_base = slugify_crop_name(name)
                    slug = slug_base
                    if StoreProduct.query.filter_by(slug=slug).first() is not None:
                        slug = f"{slug_base}-{uuid.uuid4().hex[:6]}"

                    if image_file and getattr(image_file, "filename", ""):
                        try:
                            image_url = save_product_image_upload(image_file, slug_hint=slug_base)
                        except ValueError as exc:
                            error = str(exc)

                if not error:
                    product = StoreProduct(
                        slug=slug,
                        name=name,
                        category=category if category in STORE_CATEGORY_ORDER else "Organic",
                        price=price,
                        mrp=max(int(estimate_store_mrp(price, category)), price),
                        discount_pct=compute_store_discount(price, max(int(estimate_store_mrp(price, category)), price)),
                        rating=4.2,
                        image_url=image_url,
                        description=description,
                        seller=default_store_seller(category),
                        unit="Pack",
                        stock=max(0, stock),
                        is_active=bool(is_active),
                    )
                    db.session.add(product)
                    db.session.commit()
                    success = "Product added."

        products = StoreProduct.query.order_by(StoreProduct.updated_at.desc(), StoreProduct.created_at.desc()).all()
        return render_template(
            "admin/products.html",
            products=products,
            categories=[c for c in STORE_CATEGORY_ORDER if c != "All"],
            error=error,
            success=success,
        )

    @app.route("/admin/products/<int:product_id>/edit", methods=["GET", "POST"])
    @admin_required
    def admin_edit_product(product_id):
        product = db.session.get(StoreProduct, product_id)
        if product is None:
            abort(404)

        error = None
        success = None

        if request.method == "POST":
            csrf_resp = require_csrf()
            if csrf_resp is not None:
                error = "Security check failed. Please refresh and try again."
                return render_template(
                    "admin/product_edit.html",
                    product=product,
                    categories=[c for c in STORE_CATEGORY_ORDER if c != "All"],
                    error=error,
                    success=None,
                )

            name = (request.form.get("name") or "").strip()
            category = (request.form.get("category") or product.category or "Organic").strip() or "Organic"
            image_url = (request.form.get("image_url") or "").strip()
            description = (request.form.get("description") or "").strip()
            is_active = request.form.get("is_active") == "on"
            image_file = request.files.get("image_file")

            try:
                price = int(request.form.get("price") or 0)
            except (TypeError, ValueError):
                price = 0

            try:
                stock = int(request.form.get("stock") or 0)
            except (TypeError, ValueError):
                stock = int(product.stock or 0)

            if not name or price <= 0:
                error = "Product name and price are required."
            else:
                if image_file and getattr(image_file, "filename", ""):
                    try:
                        image_url = save_product_image_upload(image_file, slug_hint=product.slug or name)
                    except ValueError as exc:
                        error = str(exc)

                product.name = name
                product.category = category if category in STORE_CATEGORY_ORDER else "Organic"
                product.price = price
                product.mrp = max(int(estimate_store_mrp(price, category)), price)
                product.discount_pct = compute_store_discount(product.price, product.mrp)
                if image_url:
                    product.image_url = image_url
                product.description = description
                product.stock = max(0, stock)
                product.is_active = bool(is_active)
                product.slug = product.slug or slugify_crop_name(name)

                if not error:
                    db.session.commit()
                    success = "Product updated."

        return render_template(
            "admin/product_edit.html",
            product=product,
            categories=[c for c in STORE_CATEGORY_ORDER if c != "All"],
            error=error,
            success=success,
        )

    @app.route("/admin/products/<int:product_id>/delete", methods=["POST"])
    @admin_required
    def admin_delete_product(product_id):
        csrf_resp = require_csrf()
        if csrf_resp is not None:
            return redirect("/admin/products")

        product = db.session.get(StoreProduct, product_id)
        if product is None:
            abort(404)
        product.is_active = False
        db.session.commit()
        return redirect("/admin/products")

    @app.route("/admin/orders", methods=["GET"])
    @admin_required
    def admin_orders():
        status_filter = (request.args.get("status") or "").strip().lower()

        orders = StoreOrder.query.order_by(StoreOrder.created_at.desc()).limit(300).all()
        if status_filter in FULFILLMENT_STATUS_ORDER:
            orders = [order for order in orders if get_fulfillment_status(order) == status_filter]

        order_rows = []
        for order in orders:
            product = getattr(order, "product", None)
            buyer = getattr(order, "buyer", None)
            order_rows.append(
                {
                    "id": order.id,
                    "product_name": getattr(product, "name", "") or "",
                    "user_name": getattr(buyer, "name", "") or "",
                    "user_email": getattr(buyer, "email", "") or "",
                    "amount_inr": (int(order.amount or 0) / 100.0),
                    "payment_status": str(order.status or ""),
                    "fulfillment_status": get_fulfillment_status(order),
                    "created_at": order.created_at,
                }
            )

        return render_template(
            "admin/orders.html",
            orders=order_rows,
            status_filter=status_filter,
            statuses=FULFILLMENT_STATUS_ORDER,
        )

    @app.route("/admin/orders/<int:order_id>/fulfillment", methods=["POST"])
    @admin_required
    def admin_update_order_fulfillment(order_id):
        csrf_resp = require_csrf()
        if csrf_resp is not None:
            return redirect("/admin/orders")

        order = db.session.get(StoreOrder, order_id)
        if order is None:
            abort(404)

        new_status = (request.form.get("fulfillment_status") or "").strip().lower()
        try:
            set_fulfillment_status(order, new_status)
        except ValueError:
            return redirect("/admin/orders")

        db.session.commit()
        return redirect("/admin/orders")

    @app.route("/admin/mappings", methods=["GET", "POST"])
    @admin_required
    def admin_mappings():
        error = None
        success = None

        if request.method == "POST":
            csrf_resp = require_csrf()
            if csrf_resp is not None:
                error = "Security check failed. Please refresh and try again."
            else:
                disease_label = (request.form.get("disease") or "").strip()
                disease_key = normalize_disease_key(disease_label)
                try:
                    product_id = int(request.form.get("product_id") or 0)
                except (TypeError, ValueError):
                    product_id = 0

                product = db.session.get(StoreProduct, product_id) if product_id else None
                if not disease_key or product is None:
                    error = "Disease name and a valid product are required."
                else:
                    existing = DiseaseProductMapping.query.filter_by(disease_key=disease_key).first()
                    if existing is None:
                        existing = DiseaseProductMapping(
                            disease_key=disease_key, disease_label=disease_label, product_id=product.id
                        )
                        db.session.add(existing)
                    else:
                        existing.disease_label = disease_label
                        existing.product_id = product.id
                    db.session.commit()
                    success = "Mapping saved."

        mappings = DiseaseProductMapping.query.order_by(DiseaseProductMapping.updated_at.desc()).all()
        products = StoreProduct.query.filter_by(is_active=True).order_by(StoreProduct.name.asc()).all()

        mapping_rows = []
        for mapping in mappings:
            mapping_rows.append(
                {
                    "id": mapping.id,
                    "disease": mapping.disease_label,
                    "disease_key": mapping.disease_key,
                    "product_id": mapping.product_id,
                    "product_name": getattr(mapping.product, "name", "") if mapping.product else "",
                    "updated_at": mapping.updated_at,
                }
            )

        return render_template(
            "admin/mappings.html",
            mappings=mapping_rows,
            products=products,
            error=error,
            success=success,
        )

    @app.route("/admin/mappings/<int:mapping_id>/delete", methods=["POST"])
    @admin_required
    def admin_delete_mapping(mapping_id):
        csrf_resp = require_csrf()
        if csrf_resp is not None:
            return redirect("/admin/mappings")

        mapping = db.session.get(DiseaseProductMapping, mapping_id)
        if mapping is None:
            abort(404)
        db.session.delete(mapping)
        db.session.commit()
        return redirect("/admin/mappings")

