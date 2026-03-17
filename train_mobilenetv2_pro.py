import os
import json
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, WeightedRandomSampler, Subset
from torch.optim.lr_scheduler import ReduceLROnPlateau
from torchvision import datasets, transforms, models
from pathlib import Path
import matplotlib.pyplot as plt

try:
    import seaborn as sns  # type: ignore
except Exception:
    sns = None

try:
    from sklearn.metrics import confusion_matrix, classification_report  # type: ignore
    from sklearn.utils.class_weight import compute_class_weight  # type: ignore
except Exception:
    confusion_matrix = None
    classification_report = None
    compute_class_weight = None
import numpy as np
from collections import Counter

# Config for AgroVision AI - High Accuracy MobileNetV2
DATASET_DIR = Path("dataset") / "PlantVillage"
MODELS_DIR = "models"
MODEL_PATH = Path(MODELS_DIR) / "agrovision_disease_model.pth"
BEST_WEIGHTS_PATH = Path(MODELS_DIR) / "agrovision_disease_model_best_weights.pth"
LABELS_PATH = Path(MODELS_DIR) / "agrovision_disease_labels.json"

IMG_SIZE = 224
BATCH_SIZE = 32
EPOCHS = 25
PATIENCE = 7
LR_INITIAL = 0.001
LR_FINE_TUNE = 1e-5
SEED = 42

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Using device: {device}")

os.makedirs(MODELS_DIR, exist_ok=True)

def resolve_imagefolder_root(path: Path) -> Path:
    # Handles common nested extraction like dataset/PlantVillage/PlantVillage/<class dirs>
    nested = path / path.name
    if nested.is_dir():
        return nested
    return path

class EarlyStopping:
    def __init__(self, patience=PATIENCE, min_delta=0):
        self.patience = patience
        self.min_delta = min_delta
        self.counter = 0
        self.best_loss = None
        self.early_stop = False

    def __call__(self, val_loss):
        if self.best_loss is None:
            self.best_loss = val_loss
        elif val_loss < self.best_loss - self.min_delta:
            self.best_loss = val_loss
            self.counter = 0
        else:
            self.counter += 1
            if self.counter >= self.patience:
                self.early_stop = True
        return self.early_stop

def main():
    # Data loading & augmentation
    print("Loading dataset...")
    torch.manual_seed(SEED)
    np.random.seed(SEED)
    transform_train = transforms.Compose([
        transforms.Resize((IMG_SIZE, IMG_SIZE)),
        transforms.RandomRotation(20),
        transforms.RandomHorizontalFlip(),
        transforms.RandomVerticalFlip(p=0.2),
        transforms.RandomResizedCrop(IMG_SIZE, scale=(0.8, 1.0)),
        transforms.ColorJitter(brightness=0.2, contrast=0.2, saturation=0.2),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    ])
    
    transform_val = transforms.Compose([
        transforms.Resize((IMG_SIZE, IMG_SIZE)),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ])

    dataset_dir = resolve_imagefolder_root(Path(DATASET_DIR))
    if not dataset_dir.exists():
        print(f"Dataset directory not found at {dataset_dir}. Please check the extraction path.")
        return

    # Meta dataset (no transforms) for stable class ordering and targets.
    meta_dataset = datasets.ImageFolder(str(dataset_dir))
    class_names = meta_dataset.classes
    num_classes = len(class_names)

    print(f"Dataset: {len(meta_dataset)} images, {num_classes} classes.")
    print(f"Dataset root: {dataset_dir}")

    # Save labels
    labels_dict = {str(i): name for i, name in enumerate(class_names)}
    with open(LABELS_PATH, "w", encoding="utf-8") as f:
        json.dump(labels_dict, f, indent=2)
    print(f"Labels saved to {LABELS_PATH}")

    # Train/val split (indices) to avoid transform leakage between subsets.
    total_size = len(meta_dataset)
    train_size = int(0.8 * total_size)
    perm = torch.randperm(total_size, generator=torch.Generator().manual_seed(SEED)).tolist()
    train_indices = perm[:train_size]
    val_indices = perm[train_size:]

    train_base = datasets.ImageFolder(str(dataset_dir), transform=transform_train)
    val_base = datasets.ImageFolder(str(dataset_dir), transform=transform_val)
    train_dataset = Subset(train_base, train_indices)
    val_dataset = Subset(val_base, val_indices)

    train_targets = [meta_dataset.targets[i] for i in train_indices]
    class_counts = Counter(train_targets)
    print("Train class distribution:", class_counts)

    # Weighted sampler for training balance (aligned to train subset order).
    class_weight_by_label = {}
    if compute_class_weight is not None:
        unique_train_classes = np.unique(train_targets)
        weights = compute_class_weight("balanced", classes=unique_train_classes, y=np.array(train_targets))
        class_weight_by_label = {
            int(cls): float(wt) for cls, wt in zip(unique_train_classes.tolist(), weights.tolist())
        }
    sample_weights = [class_weight_by_label.get(int(label), 1.0) for label in train_targets]
    sampler = WeightedRandomSampler(sample_weights, num_samples=len(sample_weights), replacement=True)

    train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, sampler=sampler, num_workers=0)
    val_loader = DataLoader(val_dataset, batch_size=BATCH_SIZE, shuffle=False, num_workers=0)

    # Model: MobileNetV2 with an even better head for complex disease patterns
    print("Loading MobileNetV2 (Pre-trained)...")
    try:
        model = models.mobilenet_v2(weights=models.MobileNetV2_Weights.IMAGENET1K_V1)
    except Exception:
        print("Warning: pretrained weights unavailable; falling back to random initialization.")
        try:
            model = models.mobilenet_v2(weights=None)
        except TypeError:
            model = models.mobilenet_v2(pretrained=False)
    
    # Freeze the backbone for initial head training
    for param in model.parameters():
        param.requires_grad = False
    
    # Premium Classifier Head: More robust and deeper
    model.classifier = nn.Sequential(
        nn.Linear(model.last_channel, 1024),
        nn.BatchNorm1d(1024),
        nn.ReLU(),
        nn.Dropout(0.5),
        nn.Linear(1024, 512),
        nn.BatchNorm1d(512),
        nn.ReLU(),
        nn.Dropout(0.4),
        nn.Linear(512, num_classes)
    )
    
    model = model.to(device)

    # Improved Stage 1 Architecture
    # Use Label Smoothing to prevent over-confidence on limited disease data
    try:
        criterion = nn.CrossEntropyLoss(label_smoothing=0.1)
    except TypeError:
        criterion = nn.CrossEntropyLoss()
    optimizer = optim.AdamW(model.classifier.parameters(), lr=LR_INITIAL, weight_decay=1e-4)
    scheduler = ReduceLROnPlateau(optimizer, mode='min', factor=0.5, patience=2)
    
    print("Stage 1: Training classifier head...")
    early_stopping = EarlyStopping()
    best_val_acc = -1.0
    
    for epoch in range(10):  # Head training epochs
        # Train
        model.train()
        train_loss, train_corrects = 0, 0
        for inputs, labels in train_loader:
            inputs, labels = inputs.to(device), labels.to(device)
            optimizer.zero_grad()
            outputs = model(inputs)
            loss = criterion(outputs, labels)
            loss.backward()
            optimizer.step()
            
            train_loss += loss.item() * inputs.size(0)
            _, preds = torch.max(outputs, 1)
            train_corrects += torch.sum(preds == labels)
        
        train_loss /= len(train_dataset)
        train_acc = float(train_corrects) / len(train_dataset)
        
        # Val
        model.eval()
        val_loss, val_corrects = 0, 0
        all_preds, all_labels = [], []
        with torch.no_grad():
            for inputs, labels in val_loader:
                inputs, labels = inputs.to(device), labels.to(device)
                outputs = model(inputs)
                loss = criterion(outputs, labels)
                
                val_loss += loss.item() * inputs.size(0)
                _, preds = torch.max(outputs, 1)
                val_corrects += torch.sum(preds == labels)
                
                all_preds.extend(preds.cpu().numpy())
                all_labels.extend(labels.cpu().numpy())
        
        val_loss /= len(val_dataset)
        val_acc = float(val_corrects) / len(val_dataset)
        scheduler.step(val_loss)
        
        print(f"Epoch {epoch+1}/10 - Train: {train_acc:.3f}, Val: {val_acc:.3f}, Loss: {val_loss:.4f}")
        
        if val_acc > best_val_acc:
            best_val_acc = val_acc
            torch.save(model.state_dict(), BEST_WEIGHTS_PATH)
        
        if early_stopping(val_loss):
            print("Early stopping!")
            break

    if BEST_WEIGHTS_PATH.exists():
        model.load_state_dict(torch.load(BEST_WEIGHTS_PATH, map_location=device))

    # Stage 2: Fine-tune last 30 layers (more depth for better feature extraction)
    print("\nStage 2: Fine-tuning last 30 layers...")
    for param in model.features[-30:].parameters():
        param.requires_grad = True
    
    # Use AdamW with small learning rate for stable fine-tuning
    optimizer = optim.AdamW(filter(lambda p: p.requires_grad, model.parameters()), lr=LR_FINE_TUNE, weight_decay=1e-5)
    scheduler = ReduceLROnPlateau(optimizer, mode='min', factor=0.2, patience=3)
    
    early_stopping = EarlyStopping(patience=10)
    for epoch in range(EPOCHS):
        model.train()
        train_loss, train_corrects = 0, 0
        for inputs, labels in train_loader:
            inputs, labels = inputs.to(device), labels.to(device)
            optimizer.zero_grad()
            outputs = model(inputs)
            loss = criterion(outputs, labels)
            loss.backward()
            optimizer.step()
            
            train_loss += loss.item() * inputs.size(0)
            _, preds = torch.max(outputs, 1)
            train_corrects += torch.sum(preds == labels)
        
        train_loss /= len(train_dataset)
        train_acc = float(train_corrects) / len(train_dataset)
        
        # Val
        model.eval()
        val_loss, val_corrects = 0, 0
        all_preds, all_labels = [], []
        with torch.no_grad():
            for inputs, labels in val_loader:
                inputs, labels = inputs.to(device), labels.to(device)
                outputs = model(inputs)
                loss = criterion(outputs, labels)

                val_loss += loss.item() * inputs.size(0)
                _, preds = torch.max(outputs, 1)
                val_corrects += torch.sum(preds == labels)
                all_preds.extend(preds.cpu().numpy())
                all_labels.extend(labels.cpu().numpy())
        
        val_loss /= len(val_dataset)
        val_acc = float(val_corrects) / len(val_dataset)
        scheduler.step(val_loss)
        
        print(f"Fine-tune Epoch {epoch+1}/{EPOCHS} - Train: {train_acc:.3f}, Val: {val_acc:.3f}")
        
        if val_acc > best_val_acc:
            best_val_acc = val_acc
            torch.save(model.state_dict(), BEST_WEIGHTS_PATH)
            print(f"New best model saved! Val Acc: {val_acc:.3f}")
        
        if early_stopping(val_loss):
            print("Fine-tune early stopping!")
            break

    if BEST_WEIGHTS_PATH.exists():
        model.load_state_dict(torch.load(BEST_WEIGHTS_PATH, map_location=device))
    torch.save(model, MODEL_PATH)

    print(f"\nFinal Best Val Accuracy: {best_val_acc:.3f}")

    # Confusion Matrix & Evaluation
    if confusion_matrix is None or classification_report is None:
        print("Skipping evaluation visuals: scikit-learn not available.")
        print(f"\nTraining complete! Model: {MODEL_PATH}")
        print("Run: python app.py to test /predict-disease endpoint")
        return

    print("\nGenerating Confusion Matrix...")
    model.eval()
    
    all_preds, all_labels = [], []
    with torch.no_grad():
        for inputs, labels in val_loader:
            inputs = inputs.to(device)
            outputs = model(inputs)
            _, preds = torch.max(outputs, 1)
            all_preds.extend(preds.cpu().numpy())
            all_labels.extend(labels.cpu().numpy())
    
    label_ids = list(range(num_classes))
    cm = confusion_matrix(all_labels, all_preds, labels=label_ids)
    plt.figure(figsize=(12, 10))
    if sns is not None:
        sns.heatmap(cm, annot=False, cmap="Blues", xticklabels=class_names, yticklabels=class_names)
    else:
        plt.imshow(cm, interpolation="nearest", cmap="Blues")
    plt.title('Confusion Matrix - AgroVision Disease Model')
    plt.ylabel('True Label')
    plt.xlabel('Predicted Label')
    plt.xticks(rotation=45, ha='right')
    plt.yticks(rotation=0)
    plt.tight_layout()
    plt.savefig('models/confusion_matrix.png', dpi=300, bbox_inches='tight')
    plt.close()
    
    print("\nClassification Report:")
    print(
        classification_report(
            all_labels,
            all_preds,
            labels=label_ids,
            target_names=class_names,
            zero_division=0,
        )
    )
    
    print(f"\nTraining complete! Model: {MODEL_PATH}")
    print("Run: python app.py to test /predict-disease endpoint")

if __name__ == "__main__":
    main()
