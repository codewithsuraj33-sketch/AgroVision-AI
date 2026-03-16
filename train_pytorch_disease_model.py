import os
import json
import torch # type: ignore
import torch.nn as nn # type: ignore
import torch.optim as optim # type: ignore
from torchvision import datasets, transforms, models # type: ignore
from torch.utils.data import DataLoader, random_split # type: ignore
from pathlib import Path

# Configuration
DATASET_DIR = "dataset/PlantVillage"
MODELS_DIR = "models"
MODEL_SAVE_PATH = os.path.join(MODELS_DIR, "crop_disease_model.pth")
LABELS_SAVE_PATH = os.path.join(MODELS_DIR, "crop_disease_labels.json")

IMG_SIZE = 224
BATCH_SIZE = 32
EPOCHS = 5

def main():
    if not os.path.exists(DATASET_DIR):
        print(f"Dataset directory not found at {DATASET_DIR}.")
        return

    os.makedirs(MODELS_DIR, exist_ok=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")

    # Data transformation and Augmentation
    data_transforms = transforms.Compose([
        transforms.Resize((IMG_SIZE, IMG_SIZE)),
        transforms.RandomHorizontalFlip(),
        transforms.RandomRotation(20),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    ])

    full_dataset = datasets.ImageFolder(DATASET_DIR, transform=data_transforms)
    class_names = full_dataset.classes
    num_classes = len(class_names)
    print(f"Found {num_classes} classes.")

    # Save Class Map
    labels_dict = {str(i): name for i, name in enumerate(class_names)}
    with open(LABELS_SAVE_PATH, 'w', encoding='utf-8') as f:
        json.dump(labels_dict, f, indent=4)

    # Split dataset 80/20
    train_size = int(0.8 * len(full_dataset))
    val_size = len(full_dataset) - train_size
    train_dataset, val_dataset = random_split(full_dataset, [train_size, val_size])

    train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True, num_workers=0)
    val_loader = DataLoader(val_dataset, batch_size=BATCH_SIZE, shuffle=False, num_workers=0)

    # Define the Model (MobileNetV2 from PyTorch)
    model = models.mobilenet_v2(pretrained=True)
    
    # Freeze base parameters
    for param in model.parameters():
        param.requires_grad = False
        
    # Replace top classifier
    model.classifier[1] = nn.Sequential(
        nn.Linear(model.last_channel, 256),
        nn.ReLU(),
        nn.Dropout(0.5),
        nn.Linear(256, num_classes)
    )

    model = model.to(device)

    criterion = nn.CrossEntropyLoss()
    optimizer = optim.Adam(model.classifier.parameters(), lr=0.001)

    print(f"Starting training for {EPOCHS} epochs...")
    best_loss = float('inf')

    for epoch in range(EPOCHS):
        model.train()
        running_loss = 0.0
        corrects = 0
        total = 0

        for inputs, labels in train_loader:
            inputs, labels = inputs.to(device), labels.to(device)

            optimizer.zero_grad()
            outputs = model(inputs)
            loss = criterion(outputs, labels)
            loss.backward()
            optimizer.step()

            running_loss += loss.item() * inputs.size(0)
            _, preds = torch.max(outputs, 1)
            corrects += torch.sum(preds == labels.data)
            total += inputs.size(0)

        epoch_loss = running_loss / total
        epoch_acc = float(corrects) / total

        print(f"Epoch {epoch+1}/{EPOCHS} - Loss: {epoch_loss:.4f} Acc: {epoch_acc:.4f}")

    print("Training finished. Saving model...")
    # Save the full model architecture and weights
    torch.save(model, MODEL_SAVE_PATH)
    print(f"Model saved to {MODEL_SAVE_PATH}")
    print(f"Labels saved to {LABELS_SAVE_PATH}")


if __name__ == "__main__":
    main()
