"""
Module nhận diện khuôn mặt sử dụng UniFace (FaceAnalyzer: SCRFD + ArcFace).
"""
import math
import cv2
import numpy as np
from uniface.constants import SCRFDWeights, ArcFaceWeights
from uniface.detection import SCRFD
from uniface.recognition import ArcFace
from uniface.spoofing import MiniFASNet

class FaceEngine:
    def __init__(self, threshold: float = 0.55, anti_spoofing: bool = False, spoof_threshold: float = 0.85):
        self.threshold = threshold
        # SCRFD 500M detector với input_size 320x320 để tối ưu tốc độ suy luận
        self.detector = SCRFD(model_name=SCRFDWeights.SCRFD_500M_KPS, input_size=(320, 320))
        self.recognizer = ArcFace(model_name=ArcFaceWeights.MNET)
        self.anti_spoofing = anti_spoofing
        self.spoof_threshold = spoof_threshold
        self._spoofer = None
        if anti_spoofing:
            self._ensure_spoofer()

    def _ensure_spoofer(self):
        """Khởi tạo mô hình MiniFASNet khi cần sử dụng."""
        if self._spoofer is None:
            self._spoofer = MiniFASNet()
        return self._spoofer

    def enable_anti_spoofing(self, enabled: bool, spoof_threshold: float = None):
        """Bật/tắt tính năng chống ảnh giả và cập nhật ngưỡng tin cậy."""
        self.anti_spoofing = enabled
        if spoof_threshold is not None:
            self.spoof_threshold = spoof_threshold
        if enabled:
            self._ensure_spoofer()

    def check_spoofing(self, frame: np.ndarray, bbox: list | np.ndarray) -> tuple[bool, float, str]:
        """
        Kiểm tra khuôn mặt có phải người thật hay giả mạo (ảnh in, màn hình, video).
        Trả về: (is_real: bool, confidence: float, status: str)
        status: "REAL", "SPOOF", "DISABLED"
        """
        if not self.anti_spoofing:
            return True, 1.0, "DISABLED"

        if frame is None or frame.size == 0 or not bbox:
            return False, 0.0, "INVALID_FRAME"

        try:
            spoofer = self._ensure_spoofer()
            result = spoofer.predict(frame, bbox)
            is_real = bool(result.is_real and result.confidence >= self.spoof_threshold)
            return is_real, float(result.confidence), ("REAL" if is_real else "SPOOF")
        except Exception as e:
            return False, 0.0, f"ERROR_{e}"

    def process_frame(self, frame: np.ndarray):
        """
        Phát hiện khuôn mặt bằng detector 320x320 (Lazy recognition: không trích xuất embedding tại đây).
        Trả về danh sách các dict chứa: bbox, confidence, landmarks, embedding (None).
        """
        if frame is None or frame.size == 0:
            return []
        
        faces = self.detector.detect(frame)
        results = []
        for face in faces:
            lmks = face.landmarks if hasattr(face, "landmarks") and face.landmarks is not None else None
            results.append({
                "bbox": [int(v) for v in face.bbox],
                "confidence": float(face.confidence),
                "landmarks": lmks,
                "embedding": getattr(face, "embedding", None)
            })
        return results

    def extract_embedding(self, frame: np.ndarray, landmarks: np.ndarray):
        """
        Trích xuất vector đặc trưng 512 chiều chuẩn hóa L2 norm từ frame và 5 landmarks.
        """
        if frame is None or frame.size == 0 or landmarks is None:
            return None
        try:
            return self.recognizer.get_normalized_embedding(frame, landmarks)
        except Exception:
            return None

    @staticmethod
    def select_primary_face(faces: list):
        """
        Chọn khuôn mặt đứng gần camera nhất (diện tích bounding box lớn nhất).
        Trả về face dict hoặc None nếu danh sách rỗng.
        """
        if not faces:
            return None
        return max(
            faces,
            key=lambda f: (f["bbox"][2] - f["bbox"][0]) * (f["bbox"][3] - f["bbox"][1])
        )

    @staticmethod
    def check_face_pose(
        face: dict,
        frame_w: int,
        frame_h: int,
        min_size_pct: float = 18.0,
        max_roll_deg: float = 15.0,
        max_yaw_pct: float = 30.0
    ):
        """
        Kiểm tra khuôn mặt có đứng đủ gần và nhìn thẳng vào camera hay không.
        - min_size_pct: Chiều cao khuôn mặt tối thiểu theo % chiều cao khung hình.
        - max_roll_deg: Góc nghiêng đầu tối đa cho phép (Roll).
        - max_yaw_pct: Độ lệch quay mặt trái/phải tối đa cho phép (Yaw).

        Trả về: (is_valid: bool, status: str, reason: str, metrics: dict)
        status: "FRONTAL_OK", "TOO_FAR", "NOT_FRONTAL"
        """
        bbox = face.get("bbox", [0, 0, 0, 0])
        x1, y1, x2, y2 = bbox
        w = max(0, x2 - x1)
        h = max(0, y2 - y1)

        size_pct = (float(h) / float(frame_h) * 100.0) if frame_h > 0 else 0.0

        metrics = {
            "size_pct": round(size_pct, 1),
            "roll_deg": 0.0,
            "yaw_pct": 0.0
        }

        # 1. Kiểm tra khoảng cách / kích thước mặt
        if size_pct < min_size_pct:
            return False, "TOO_FAR", f"Vui lòng lại gần camera ({size_pct:.1f}% < {min_size_pct:.0f}%)", metrics

        # 2. Kiểm tra landmarks để tính toán góc nhìn thẳng
        landmarks = face.get("landmarks")
        if landmarks is None or len(landmarks) < 5:
            return True, "FRONTAL_OK", "Nhìn thẳng (Không có landmarks)", metrics

        # 5 điểm chuẩn SCRFD: [left_eye, right_eye, nose, mouth_left, mouth_right]
        left_eye = landmarks[0]
        right_eye = landmarks[1]
        nose = landmarks[2]

        # a. Tính góc nghiêng đầu (Roll): góc giữa đoạn nối 2 mắt và phương ngang
        dx = float(right_eye[0] - left_eye[0])
        dy = float(right_eye[1] - left_eye[1])
        roll_deg = abs(math.degrees(math.atan2(dy, dx))) if (dx != 0 or dy != 0) else 0.0
        metrics["roll_deg"] = round(roll_deg, 1)

        if roll_deg > max_roll_deg:
            return False, "NOT_FRONTAL", f"Vui lòng giữ thẳng đầu (Nghiêng {roll_deg:.1f}° > {max_roll_deg:.0f}°)", metrics

        # b. Tính độ lệch quay mặt trái/phải (Yaw): độ bất đối xứng giữa mũi và 2 mắt
        d_left = abs(float(nose[0] - left_eye[0]))
        d_right = abs(float(right_eye[0] - nose[0]))
        d_total = d_left + d_right
        yaw_pct = (abs(d_left - d_right) / d_total * 100.0) if d_total > 0 else 0.0
        metrics["yaw_pct"] = round(yaw_pct, 1)

        if yaw_pct > max_yaw_pct:
            return False, "NOT_FRONTAL", f"Vui lòng nhìn thẳng vào camera (Lệch {yaw_pct:.1f}% > {max_yaw_pct:.0f}%)", metrics

        return True, "FRONTAL_OK", "Nhìn thẳng hợp lệ", metrics

    def extract_single_face(self, frame: np.ndarray):
        """
        Dùng cho màn hình Đăng ký: Trích xuất khuôn mặt rõ nhất/lớn nhất trong khung hình.
        Trả về: (embedding, bbox, confidence) hoặc (None, None, 0.0)
        """
        faces = self.process_frame(frame)
        if not faces:
            return None, None, 0.0
        
        largest_face = self.select_primary_face(faces)
        embedding = self.extract_embedding(frame, largest_face.get("landmarks"))
        largest_face["embedding"] = embedding
        return embedding, largest_face["bbox"], largest_face["confidence"]

    def match(self, query_embedding: np.ndarray, student_cache: dict, custom_threshold: float = None):
        """
        So khớp vector truy vấn với danh bạ sinh viên trong RAM.
        Trả về: (student_dict, max_score) hoặc (None, max_score)
        """
        if not student_cache or query_embedding is None:
            return None, 0.0
        
        thresh = custom_threshold if custom_threshold is not None else self.threshold
        best_student = None
        best_score = -1.0

        for student_id, student in student_cache.items():
            db_emb = student["embedding"]
            # Cosine similarity trên vector đã chuẩn hóa L2 norm = Dot product
            score = float(np.dot(query_embedding, db_emb))
            if score > best_score:
                best_score = score
                best_student = student

        if best_score >= thresh:
            return best_student, best_score
        return None, best_score
