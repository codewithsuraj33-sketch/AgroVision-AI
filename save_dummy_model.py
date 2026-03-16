import os
import json
import torch # type: ignore
import torch.nn as nn # type: ignore
from torchvision import models # type: ignore

MODELS_DIR = "models"
MODEL_SAVE_PATH = os.path.join(MODELS_DIR, "crop_disease_model.pth")
LABELS_SAVE_PATH = os.path.join(MODELS_DIR, "crop_disease_labels.json")

def main():
    os.makedirs(MODELS_DIR, exist_ok=True)
    
    # We found 16 classes in the dataset earlier
    num_classes = 16
    
    print("Building dummy model...")
    model = models.mobilenet_v2(pretrained=False)
    
    # Replace top classifier
    model.classifier[1] = nn.Sequential(
        nn.Linear(model.last_channel, 256),
        nn.ReLU(),
        nn.Dropout(0.5),
        nn.Linear(256, num_classes)
    )
    
    print("Saving dummy untrained model...")
    torch.save(model, MODEL_SAVE_PATH)
    
    # Save a generic class map just to unblock the API
    labels_dict = {str(i): f"Disease_Class_{i}" for i in range(num_classes)}
    if os.path.exists("dataset/PlantVillage"):
        classes = sorted(os.listdir("dataset/PlantVillage"))
        if len(classes) == num_classes:
            labels_dict = {str(i): name for i, name in enumerate(classes)}

    with open(LABELS_SAVE_PATH, 'w', encoding='utf-8') as f:
        json.dump(labels_dict, f, indent=4)
        
    print(f"Dummy model saved to {MODEL_SAVE_PATH}")

if __name__ == "__main__":
    main()
