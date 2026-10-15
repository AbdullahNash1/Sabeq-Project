from pathlib import Path
import argparse

import cv2
from ultralytics import YOLO


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv"}


def detect_people(frame, model, confidence=0.65):
    """
    Detect people in a single frame.

    Returns:
        annotated_frame: frame with bounding boxes
        person_count: detected person count
    """

    result = model.predict(
    source=frame,
    classes=[0],
    conf=confidence,
    imgsz=1280,
    verbose=False
     )[0]

    person_count = len(result.boxes)

    annotated_frame = result.plot()

    cv2.putText(
        annotated_frame,
        f"People: {person_count}",
        (20, 45),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.2,
        (0, 0, 255),
        3
    )

    return annotated_frame, person_count


def process_image(source, model, output_path, confidence):
    image = cv2.imread(source)

    if image is None:
        raise RuntimeError(f"Could not open image: {source}")

    annotated_image, person_count = detect_people(
        image,
        model,
        confidence
    )

    Path(output_path).parent.mkdir(
        parents=True,
        exist_ok=True
    )

    cv2.imwrite(output_path, annotated_image)

    print(f"People detected: {person_count}")
    print(f"Output saved to: {output_path}")


def process_video(source, model, output_path, confidence):
    cap = cv2.VideoCapture(source)

    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {source}")

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)

    if fps <= 0:
        fps = 30

    Path(output_path).parent.mkdir(
        parents=True,
        exist_ok=True
    )

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")

    writer = cv2.VideoWriter(
        output_path,
        fourcc,
        fps,
        (width, height)
    )

    frame_number = 0

    while True:
        success, frame = cap.read()

        if not success:
            break

        annotated_frame, person_count = detect_people(
            frame,
            model,
            confidence
        )

        writer.write(annotated_frame)

        print(
            f"Frame {frame_number}: "
            f"{person_count} people detected"
        )

        frame_number += 1

    cap.release()
    writer.release()

    print("Video processing completed.")
    print(f"Output saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Sabeq AI - Person Detection"
    )

    parser.add_argument(
        "--source",
        required=True,
        help="Input image or video path"
    )

    parser.add_argument(
        "--model",
        default="yolo26s.pt",
        help="YOLO model path"
    )

    parser.add_argument(
        "--conf",
        type=float,
        default=0.35,
        help="Detection confidence threshold"
    )

    args = parser.parse_args()

    source_path = Path(args.source)

    if not source_path.exists():
        raise FileNotFoundError(
            f"Source file not found: {args.source}"
        )

    extension = source_path.suffix.lower()

    model = YOLO(args.model)

    if extension in IMAGE_EXTENSIONS:

        output_path = (
            Path("output")
            / f"{source_path.stem}_detected.jpg"
        )

        process_image(
            str(source_path),
            model,
            str(output_path),
            args.conf
        )

    elif extension in VIDEO_EXTENSIONS:

        output_path = (
            Path("output")
            / f"{source_path.stem}_detected.mp4"
        )

        process_video(
            str(source_path),
            model,
            str(output_path),
            args.conf
        )

    else:
        raise ValueError(
            f"Unsupported file type: {extension}"
        )


if __name__ == "__main__":
    main()