import os
import json
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, WeightedRandomSampler, random_split
from torch.optim.lr_scheduler import ReduceLROnPlateau
from torchvision import datasets, transforms, models
from pathlib import Path
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.metrics import confusion_matrix, classification_report
from sklearn.utils.class_weight import compute_class_weight
import numpy as np
from collections import Counter

# Config for AgroVision AI - High Accuracy MobileNetV2
DATASET_DIR = "dataset/PlantVillage"
MODELS_DIR = "models"
MODEL_PATH = Path(MODELS_DIR) / "agrovision_disease_model.pth"
LABELS_PATH = Path(MODELS_DIR) / "agrovision_disease_labels.json"

IMG_SIZE = 224
BATCH_SIZE = 32
EPOCHS = 25
PATIENCE = 7
LR_INITIAL = 0.001
LR_FINE_TUNE = 1e-5

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Using device: {device}")

os.makedirs(MODELS_DIR, exist_ok=True)

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
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    ])

    full_dataset = datasets.ImageFolder(DATASET_DIR, transform=transform_train)
    class_names = full_dataset.classes
    num_classes = len(class_names)
    
    print(f"Dataset: {len(full_dataset)} images, {num_classes} classes: {class_names}")

    # Save labels
    labels_dict = {str(i): name for i, name in enumerate(class_names)}
    with open(LABELS_PATH, 'w') as f:
        json.dump(labels_dict, f, indent=2)
    print(f"Labels saved to {LABELS_PATH}")

    # Class balancing
    class_counts = Counter(full_dataset.targets)
    print("Class distribution:", class_counts)
    
    # Split 80/20
    train_size = int(0.8 * len(full_dataset))
    val_size = len(full_dataset) - train_size
    train_dataset, val_dataset = random_split(full_dataset, [train_size, val_size])
    
    # Validation uses deterministic transform
    val_dataset.dataset.transform = transform_val
    
    # Weighted sampler for training balance
    class_weights = compute_class_weight('balanced', classes=np.unique(train_dataset.dataset.targets), y=train_dataset.dataset.targets)
    sample_weights = [class_weights[label] for label in train_dataset.dataset.targets]
    sampler = WeightedRandomSampler(sample_weights, len(sample_weights))
    
    train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, sampler=sampler, num_workers=0)
    val_loader = DataLoader(val_dataset, batch_size=BATCH_SIZE, shuffle=False, num_workers=0)

    # Model: MobileNetV2 transfer learning
    print("Loading MobileNetV2...")
    model = models.mobilenet_v2(pretrained=True)
    
    # Freeze all initially
    for param in model.parameters():
        param.requires_grad = False
    
    # Custom head
    model.classifier = nn.Sequential(
        nn.Dropout(0.4),
        nn.Linear(model.last_channel, 512),
        nn.ReLU(),
        nn.Dropout(0.4),
        nn.Linear(512, 256),
        nn.ReLU(),
        nn.Dropout(0.3),
        nn.Linear(256, num_classes)
    )
    
    model = model.to(device)

    # Stage 1: Train head only
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.Adam(model.classifier.parameters(), lr=LR_INITIAL)
    scheduler = ReduceLROnPlateau(optimizer, mode='min', factor=0.5, patience=3)
    
    print("Stage 1: Training classifier head...")
    early_stopping = EarlyStopping()
    best_val_acc = 0
    
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
            torch.save(model.state_dict(), MODEL_PATH)
        
        if early_stopping(val_loss):
            print("Early stopping!")
            break

    # Stage 2: Fine-tune last 20 layers
    print("\nStage 2: Fine-tuning last 20 layers...")
    for param in model.features[-20:].parameters():  # Last 20 layers
        param.requires_grad = True
    
    optimizer = optim.Adam(filter(lambda p: p.requires_grad, model.parameters()), lr=LR_FINE_TUNE)
    scheduler = ReduceLROnPlateau(optimizer, mode='min', factor=0.2, patience=4)
    
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
            
            train_loss += loss.item()
            _, preds = torch.max(outputs, 1)
            train_corrects += torch.sum(preds == labels)
        
        train_loss /= len(train_loader)
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
                
                val_loss += loss.item()
                _, preds = torch.max(outputs, 1)
                val_corrects += torch.sum(preds == labels)
                all_preds.extend(preds.cpu().numpy())
                all_labels.extend(labels.cpu().numpy())
        
        val_loss /= len(val_loader)
        val_acc = float(val_corrects) / len(val_dataset)
        scheduler.step(val_loss)
        
        print(f"Fine-tune Epoch {epoch+1}/{EPOCHS} - Train: {train_acc:.3f}, Val: {val_acc:.3f}")
        
        if val_acc > best_val_acc:
            best_val_acc = val_acc
            torch.save(model, MODEL_PATH)
            print(f"New best model saved! Val Acc: {val_acc:.3f}")
        
        if early_stopping(val_loss):
            print("Fine-tune early stopping!")
            break

    print(f"\nFinal Best Val Accuracy: {best_val_acc:.3f}")

    # Confusion Matrix & Evaluation
    print("\nGenerating Confusion Matrix...")
    model.load_state_dict(torch.load(MODEL_PATH, map_location=device))
    model.eval()
    
    all_preds, all_labels = [], []
    with torch.no_grad():
        for inputs, labels in val_loader:
            inputs = inputs.to(device)
            outputs = model(inputs)
            _, preds = torch.max(outputs, 1)
            all_preds.extend(preds.cpu().numpy())
            all_labels.extend(labels.cpu().numpy())
    
    cm = confusion_matrix(all_labels, all_preds)
    plt.figure(figsize=(12, 10))
    sns.heatmap(cm, annot=True, fmt='d', cmap='Blues', xticklabels=class_names, yticklabels=class_names)
    plt.title('Confusion Matrix - AgroVision Disease Model')
    plt.ylabel('True Label')
    plt.xlabel('Predicted Label')
    plt.xticks(rotation=45, ha='right')
    plt.yticks(rotation=0)
    plt.tight_layout()
    plt.savefig('models/confusion_matrix.png', dpi=300, bbox_inches='tight')
    plt.show()
    
    print("\nClassification Report:")
    print(classification_report(all_labels, all_preds, target_names=class_names))
    
    print(f"\n✅ Training complete! Model: {MODEL_PATH}")
    print("Run: python app.py to test /predict-disease endpoint")

if __name__ == "__main__":
    main()
