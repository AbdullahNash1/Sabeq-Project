import os

import torch
from ultralytics import YOLO


def limit_cpu_threads(threads=0):
    """On CPU, keep YOLO from taking every core away from Gazebo's camera rendering.

    threads=0 means half the cores (at least 2). No effect when CUDA is available.
    Returns the device YOLO will run on, for logging.
    """
    if torch.cuda.is_available():
        return "cuda"
    threads = threads or max(2, (os.cpu_count() or 4) // 2)
    torch.set_num_threads(threads)
    return f"cpu ({threads} threads)"


class PersonDetector:
    def __init__(
        self,
        model_path: str,
        confidence: float = 0.30,
        image_size: int = 1280,
        max_detections: int = 500,
    ):
        self.model = YOLO(model_path)

        self.confidence = confidence
        self.image_size = image_size
        self.max_detections = max_detections

    def detect(self, frame):
        result = self.model.predict(
            source=frame,

            # Detect people only
            classes=[0],

            # Lower than the old 0.65
            # to keep distant / small people
            conf=self.confidence,

            # Important for small aerial targets
            imgsz=self.image_size,

            # Plenty for our simulated crowds
            max_det=self.max_detections,

            verbose=False,
        )[0]

        detections = []

        for box in result.boxes:
            x1, y1, x2, y2 = box.xyxy[0].tolist()

            detections.append(
                {
                    "bbox": [
                        float(x1),
                        float(y1),
                        float(x2),
                        float(y2),
                    ],
                    "confidence": float(box.conf[0]),
                }
            )

        return detections