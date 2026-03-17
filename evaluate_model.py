import torch
from torch.utils.data import DataLoader
from torchvision import transforms, datasets
import json
from pathlib import Path
from sklearn.metrics import classification_report
import numpy as np
from PIL import Image
import matplotlib.pyplot as plt

MODEL_PATH = "models/agrovision_disease_model.pth"
LABELS_PATH = "models/agrovision_disease_labels.json"
DATASET_DIR = Path("dataset") / "PlantVillage"

def resolve_imagefolder_root(path: Path) -> Path:
    nested = path / path.name
    if nested.is_dir():
        return nested
    return path

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
transform = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
])

# Load model & labels
if not Path(MODEL_PATH).exists():
    raise FileNotFoundError(f"Missing model file: {MODEL_PATH}. Train it first with train_mobilenetv2_pro.py")
if not Path(LABELS_PATH).exists():
    raise FileNotFoundError(f"Missing labels file: {LABELS_PATH}. Train it first with train_mobilenetv2_pro.py")

model = torch.load(MODEL_PATH, map_location=device)
model.eval()
with open(LABELS_PATH, 'r') as f:
    labels = json.load(f)
class_names = list(labels.values())
num_classes = len(class_names)

print("Model loaded. Testing predictions...")
print("Classes:", class_names)

# Test on few val images
dataset_dir = resolve_imagefolder_root(Path(DATASET_DIR))
if not dataset_dir.exists():
    raise FileNotFoundError(f"Missing dataset directory: {dataset_dir}")
dataset = datasets.ImageFolder(str(dataset_dir), transform=transform)
val_loader = DataLoader(dataset, batch_size=32, shuffle=True)

all_preds, all_labels = [], []
with torch.no_grad():
    for batch_idx, (images, labels_batch) in enumerate(val_loader):
        if batch_idx >= 10:  # Test 10 batches
            break
        images = images.to(device)
        outputs = model(images)
        _, preds = torch.max(outputs, 1)
        all_preds.extend(preds.cpu().numpy())
        all_labels.extend(labels_batch.numpy())

print("\nVal Accuracy:", np.mean(np.array(all_preds) == np.array(all_labels)))
print("\nClassification Report:")
label_ids = list(range(num_classes))
print(classification_report(all_labels, all_preds, labels=label_ids, target_names=class_names, zero_division=0))

print("\n✅ Model evaluation complete. Ready for app.py integration!")
