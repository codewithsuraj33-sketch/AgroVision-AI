import os
import json
import tensorflow as tf # type: ignore
from tensorflow.keras.preprocessing.image import ImageDataGenerator # type: ignore
from tensorflow.keras.applications import MobileNetV2 # type: ignore
from tensorflow.keras.layers import Dense, GlobalAveragePooling2D, Dropout # type: ignore
from tensorflow.keras.models import Model # type: ignore
from tensorflow.keras.optimizers import Adam # type: ignore

# Configuration
DATASET_DIR = "dataset"
PLANT_VILLAGE_DIR = os.path.join(DATASET_DIR, "PlantVillage")
MODELS_DIR = "models"
MODEL_SAVE_PATH = os.path.join(MODELS_DIR, "crop_disease_model.h5")
LABELS_SAVE_PATH = os.path.join(MODELS_DIR, "crop_disease_labels.json")

IMG_WIDTH, IMG_HEIGHT = 224, 224
BATCH_SIZE = 32
EPOCHS = 5 # Reduced for quick training, could be higher for better accuracy

def main():
    if not os.path.exists(PLANT_VILLAGE_DIR):
        print(f"Dataset directory not found at {PLANT_VILLAGE_DIR}. Please check the extraction path.")
        return

    os.makedirs(MODELS_DIR, exist_ok=True)

    print("Setting up data generators...")
    # Data Augmentation & Normalization for training
    train_datagen = ImageDataGenerator(
        rescale=1./255,
        rotation_range=20,
        width_shift_range=0.2,
        height_shift_range=0.2,
        shear_range=0.2,
        zoom_range=0.2,
        horizontal_flip=True,
        validation_split=0.2 # 80% training, 20% validation
    )

    train_generator = train_datagen.flow_from_directory(
        PLANT_VILLAGE_DIR,
        target_size=(IMG_HEIGHT, IMG_WIDTH),
        batch_size=BATCH_SIZE,
        class_mode='categorical',
        subset='training'
    )

    validation_generator = train_datagen.flow_from_directory(
        PLANT_VILLAGE_DIR,
        target_size=(IMG_HEIGHT, IMG_WIDTH),
        batch_size=BATCH_SIZE,
        class_mode='categorical',
        subset='validation'
    )

    class_indices = train_generator.class_indices
    # Reverse mapping: index to class name
    labels_dict = {str(v): k for k, v in class_indices.items()}
    
    print("Saving class labels...")
    with open(LABELS_SAVE_PATH, 'w', encoding='utf-8') as f:
        json.dump(labels_dict, f, indent=4)
        
    num_classes = len(class_indices)
    print(f"Found {num_classes} classes.")

    print("Building model...")
    # Load MobileNetV2 without the top classification layer
    base_model = MobileNetV2(
        weights='imagenet', 
        include_top=False, 
        input_shape=(IMG_HEIGHT, IMG_WIDTH, 3)
    )

    # Freeze base model layers initially
    base_model.trainable = False

    # Add custom head
    x = base_model.output
    x = GlobalAveragePooling2D()(x)
    x = Dense(256, activation='relu')(x)
    x = Dropout(0.5)(x)
    predictions = Dense(num_classes, activation='softmax')(x)

    model = Model(inputs=base_model.input, outputs=predictions)

    model.compile(
        optimizer=Adam(learning_rate=0.001),
        loss='categorical_crossentropy',
        metrics=['accuracy']
    )

    print(f"Starting training for {EPOCHS} epochs...")
    history = model.fit(
        train_generator,
        steps_per_epoch=train_generator.samples // BATCH_SIZE,
        validation_data=validation_generator,
        validation_steps=validation_generator.samples // BATCH_SIZE,
        epochs=EPOCHS
    )

    print("Training finished. Saving model...")
    model.save(MODEL_SAVE_PATH)
    print(f"Model saved to {MODEL_SAVE_PATH}")
    print(f"Labels saved to {LABELS_SAVE_PATH}")

if __name__ == "__main__":
    main()
