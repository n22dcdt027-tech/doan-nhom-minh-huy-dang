"""
Luồng đọc và xử lý camera sử dụng PyQt6 QThread và OpenCV.
"""
import time
import cv2
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QImage

import collections
import os
os.environ["OPENCV_LOG_LEVEL"] = "ERROR"

def _safe_open_capture(src):
    """Mở VideoCapture an toàn: ưu tiên backend mặc định (MSMF/DirectShow tự động) để tránh lỗi DSHOW C++."""
    if src is None:
        return None
    if isinstance(src, str):
        src = src.strip()
        if not src:
            return None
        if src.isdigit():
            src = int(src)

    try:
        cap = cv2.VideoCapture(src)
        if cap.isOpened():
            return cap
        cap.release()
    except Exception:
        pass

    if isinstance(src, int):
        try:
            cap = cv2.VideoCapture(src, cv2.CAP_DSHOW)
            if cap.isOpened():
                return cap
            cap.release()
        except Exception:
            pass
    return None

def bgr_to_qimage(frame_bgr: np.ndarray) -> QImage:
    """Chuyển đổi khung hình OpenCV BGR sang QImage RGB888."""
    rgb_frame = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb_frame.shape
    return QImage(rgb_frame.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()

class CameraWorker(QThread):
    frame_ready = pyqtSignal(QImage, str)  # (QImage, camera_type)
    student_detected = pyqtSignal(dict, str)  # (student_dict, camera_type)
    stranger_detected = pyqtSignal(str)  # camera_type
    no_face_detected = pyqtSignal(str)  # camera_type

    def __init__(self, camera_source, camera_type: str, face_engine, get_student_cache_func, db=None, parent=None):
        super().__init__(parent)
        self.camera_source = camera_source
        self.camera_type = camera_type  # "IN" hoặc "OUT"
        self.face_engine = face_engine
        self.get_student_cache = get_student_cache_func
        self.db = db
        self.running = False
        self.skip_frames = 2  # Nhận diện AI mỗi 2 frame để tối ưu FPS mượt mà
        self.pause_attendance = False  # Tạm dừng quét điểm danh khi đang mở modal đăng ký
        self.latest_raw_frame = None
        self.has_signal = False
        self.last_frame_time = 0.0

        # Cấu hình bộ lọc khuôn mặt gần nhất & nhìn thẳng
        self.min_face_size_pct = 18.0
        self.max_roll_deg = 15.0
        self.max_yaw_pct = 30.0
        self.load_filter_settings()

        # Hiệu năng xử lý & thời gian suy luận (FPS & Latency)
        self.current_fps = 0.0
        self.current_infer_ms = 0.0

    def is_signal_active(self) -> bool:
        """Kiểm tra luồng camera hiện tại có tín hiệu hình ảnh thực tế đang hoạt động hay không."""
        return bool(
            self.running 
            and getattr(self, "has_signal", False) 
            and self.latest_raw_frame is not None 
            and (time.time() - getattr(self, "last_frame_time", 0.0) < 3.0)
        )

    def load_filter_settings(self):
        """Nạp các ngưỡng lọc khuôn mặt và chống giả mạo từ CSDL."""
        if self.db:
            try:
                self.min_face_size_pct = float(self.db.get_setting("min_face_size_pct", "18"))
                self.max_roll_deg = float(self.db.get_setting("max_roll_deg", "15"))
                self.max_yaw_pct = float(self.db.get_setting("max_yaw_pct", "30"))
                as_enabled = self.db.get_setting("anti_spoofing_enabled", "0") == "1"
                as_thresh = float(self.db.get_setting("spoof_threshold", "0.85"))
                if hasattr(self.face_engine, "enable_anti_spoofing"):
                    self.face_engine.enable_anti_spoofing(as_enabled, as_thresh)
            except Exception:
                pass

    def update_filter_settings(self, min_size_pct: float, max_roll_deg: float, max_yaw_pct: float):
        """Cập nhật tức thì các ngưỡng lọc mà không cần khởi động lại thread."""
        self.min_face_size_pct = float(min_size_pct)
        self.max_roll_deg = float(max_roll_deg)
        self.max_yaw_pct = float(max_yaw_pct)

    def get_latest_frame(self):
        """Lấy khung hình thô mới nhất an toàn mà không mở thêm VideoCapture."""
        if self.latest_raw_frame is not None:
            return self.latest_raw_frame.copy()
        return None

    def set_source(self, new_source):
        self.camera_source = new_source
        self.has_signal = False
        self.latest_raw_frame = None
        self.last_frame_time = 0.0

    def stop(self):
        self.running = False
        self.has_signal = False
        self.latest_raw_frame = None
        self.current_fps = 0.0
        self.current_infer_ms = 0.0
        self.wait(2000)

    def run(self):
        self.running = True

        current_src = self.camera_source
        src = current_src
        if isinstance(src, str):
            src = src.strip()
            if src.isdigit():
                src = int(src)

        cap = _safe_open_capture(src)
        frame_idx = 0
        last_faces = []
        last_retry_time = 0
        prev_fps_time = time.perf_counter()
        read_fail_count = 0

        while self.running:
            if self.camera_source != current_src:
                current_src = self.camera_source
                src = current_src
                if isinstance(src, str):
                    src = src.strip()
                    if src.isdigit():
                        src = int(src)
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
                cap = _safe_open_capture(src)
                last_faces.clear()
                read_fail_count = 0

            if cap is None or not cap.isOpened():
                self.current_fps = 0.0
                self.current_infer_ms = 0.0
                self.has_signal = False
                self.latest_raw_frame = None
                # Tạo khung hình 16:9 thông báo lỗi không có tín hiệu camera
                blank = np.zeros((360, 640, 3), dtype=np.uint8)
                blank[:] = (22, 22, 26)  # Nền xám đen kỹ thuật
                cv2.rectangle(blank, (6, 6), (633, 353), (0, 0, 220), 2)
                cv2.rectangle(blank, (10, 10), (629, 349), (0, 0, 100), 1)

                cv2.putText(blank, "KHONG CO TIN HIEU CAMERA (MAT KET NOI)", (35, 120),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 255), 2)
                cam_name = "Camera Cua: Diem danh sinh vien" if self.camera_type == "IN" else f"Camera {self.camera_type}"
                cv2.putText(blank, cam_name, (35, 165),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 220, 255), 1)
                src_disp = str(self.camera_source).strip() if (self.camera_source is not None and str(self.camera_source).strip()) else "Chua cau hinh"
                cv2.putText(blank, f"Nguon cau hinh: {src_disp}", (35, 205),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (220, 220, 220), 1)
                cv2.putText(blank, "He thong dang thu ket noi lai... Vui long kiem tra nguon video / IP / thiet bi.", (35, 245),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.42, (160, 160, 160), 1)
                self._emit_frame(blank)
                
                # Thử kết nối lại mỗi 3 giây thay vì spam liên tục
                now_t = time.time()
                if now_t - last_retry_time > 3.0:
                    last_retry_time = now_t
                    cap = _safe_open_capture(src)
                time.sleep(0.5)
                continue

            ret, frame = cap.read()
            if not ret or frame is None:
                if isinstance(src, str) and not src.isdigit() and cap is not None and not src.startswith("http") and not src.startswith("rtsp"):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ret, frame = cap.read()
                if not ret or frame is None:
                    self.has_signal = False
                    read_fail_count += 1
                if read_fail_count >= 5:
                    self.latest_raw_frame = None
                if read_fail_count >= 30:
                    try:
                        cap.release()
                    except Exception:
                        pass
                    cap = None
                    read_fail_count = 0
                time.sleep(0.05)
                continue
            read_fail_count = 0

            # Lật ngang nếu nguồn là webcam vật lý để loại bỏ hiệu ứng gương ngược hướng
            if isinstance(src, int) or (isinstance(src, str) and src.strip().isdigit()):
                frame = cv2.flip(frame, 1)

            # Đo đạc FPS thời gian thực (làm mượt EMA)
            now_perf = time.perf_counter()
            dt = now_perf - prev_fps_time
            prev_fps_time = now_perf
            if dt > 0:
                inst_fps = 1.0 / dt
                self.current_fps = 0.9 * self.current_fps + 0.1 * inst_fps if self.current_fps > 0 else inst_fps

            self.has_signal = True
            self.last_frame_time = time.time()
            self.latest_raw_frame = frame
            frame_idx += 1
            # Xử lý nhận diện AI theo chu kỳ skip_frames nếu không bị pause
            if frame_idx % self.skip_frames == 0 and not self.pause_attendance:
                t_infer_start = time.perf_counter()
                try:
                    last_faces = self.face_engine.process_frame(frame)
                    cache = self.get_student_cache()
                    h_frame, w_frame = frame.shape[:2]

                    if last_faces:
                        # Chỉ chọn khuôn mặt lớn nhất (gần camera nhất) để xử lý
                        primary_face = self.face_engine.select_primary_face(last_faces)
                        has_valid_detection = False

                        for f in last_faces:
                            if f is primary_face:
                                f["is_primary"] = True
                                is_valid, status, reason, metrics = self.face_engine.check_face_pose(
                                    f, w_frame, h_frame,
                                    self.min_face_size_pct,
                                    self.max_roll_deg,
                                    self.max_yaw_pct
                                )
                                f["pose_valid"] = is_valid
                                f["status"] = status
                                f["reason"] = reason
                                f["metrics"] = metrics

                                if is_valid:
                                    is_real, spoof_conf, spoof_status = self.face_engine.check_spoofing(frame, f["bbox"])
                                    f["is_real"] = is_real
                                    f["spoof_conf"] = spoof_conf
                                    f["spoof_status"] = spoof_status

                                    if not is_real:
                                        f["status"] = "SPOOF"
                                        f["reason"] = f"Phat hien gia mao ({spoof_conf:.0%})"
                                        f["student"] = None
                                        f["score"] = 0.0
                                    else:
                                        emb = f.get("embedding")
                                        if emb is None and hasattr(self.face_engine, "extract_embedding"):
                                            emb = self.face_engine.extract_embedding(frame, f.get("landmarks"))
                                            f["embedding"] = emb
                                        student, score = self.face_engine.match(emb, cache)
                                        f["student"] = student
                                        f["score"] = score
                                        if student:
                                            self.student_detected.emit(student, self.camera_type)
                                        else:
                                            self.stranger_detected.emit(self.camera_type)
                                        has_valid_detection = True
                                else:
                                    f["student"] = None
                                    f["score"] = 0.0
                            else:
                                f["is_primary"] = False
                                f["status"] = "IGNORED"
                                f["reason"] = "Bo qua"

                        if not has_valid_detection:
                            self.no_face_detected.emit(self.camera_type)
                    else:
                        self.no_face_detected.emit(self.camera_type)
                except Exception as ex:
                    print(f"Lỗi inference camera {self.camera_type}: {ex}")
                finally:
                    infer_time = (time.perf_counter() - t_infer_start) * 1000.0
                    self.current_infer_ms = 0.85 * self.current_infer_ms + 0.15 * infer_time if self.current_infer_ms > 0 else infer_time

            # Vẽ bounding box và nhãn lên khung hình
            annotated = frame.copy()
            for f in last_faces:
                x1, y1, x2, y2 = f["bbox"]
                if not f.get("is_primary", False):
                    # Khuôn mặt phụ (người ở xa / người đi phía sau)
                    color = (120, 120, 120)  # Xám
                    label = "Bo qua"
                    cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 1)
                    cv2.putText(annotated, label, (x1, max(18, y1 - 6)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
                    continue

                # Khuôn mặt chính gần nhất
                status = f.get("status")
                metrics = f.get("metrics", {})

                if status == "TOO_FAR":
                    color = (0, 140, 255)  # Cam: Quá xa
                    cur_sz = metrics.get("size_pct", 0.0)
                    label = f"Vui long lai gan ({cur_sz:.0f}% / {self.min_face_size_pct:.0f}%)"
                elif status == "NOT_FRONTAL":
                    color = (0, 220, 255)  # Vàng: Chưa nhìn thẳng
                    roll = metrics.get("roll_deg", 0.0)
                    yaw = metrics.get("yaw_pct", 0.0)
                    label = f"Vui long nhin thang (Nghieng: {roll:.0f} deg, Lech: {yaw:.0f}%)"
                elif status == "SPOOF":
                    color = (0, 0, 255)  # Đỏ: Ảnh/video giả mạo
                    sp_conf = f.get("spoof_conf", 0.0)
                    label = f"CANH BAO: ANH GIA MAO ({sp_conf:.0%})"
                else:  # FRONTAL_OK
                    student = f.get("student")
                    score = f.get("score", 0.0)
                    if student:
                        color = (0, 255, 0)  # Xanh lá: Nhận diện thành công
                        label = f"{student['student_code']} - {student['full_name']} ({score:.2f})"
                    else:
                        color = (0, 0, 255)  # Đỏ: Người lạ / Chưa khớp
                        label = f"Nguoi la ({score:.2f})"

                cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
                cv2.putText(annotated, label, (x1, max(20, y1 - 8)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

            self._emit_frame(annotated)
            time.sleep(0.02)  # Khoảng ~30-40 FPS

        if cap is not None:
            cap.release()

    def _emit_frame(self, frame_bgr):
        self.frame_ready.emit(bgr_to_qimage(frame_bgr), self.camera_type)


def compute_modal_headcount(counts) -> int:
    """
    Tính số người xuất hiện nhiều nhất trong một khoảng thời gian rất ngắn (Statistical Mode).
    Ví dụ: trong 1 giây 30 FPS có 20 frame hiện 8 người, 5 frame hiện 7 người, 5 frame hiện 9 người
    -> chọn đếm 8 người trong 1 giây đó.
    Trong trường hợp số frame giữa các số người bằng nhau (tie), chọn số người lớn nhất/cao nhất (max tie-breaker).
    """
    if not counts:
        return 0
    freq = collections.Counter(counts)
    max_freq = max(freq.values())
    candidates = [c for c, count in freq.items() if count == max_freq]
    return max(candidates)


class PersonCountWorker(QThread):
    """
    Luồng đọc và đếm số lượng người (sinh viên) thời gian thực sử dụng YOLOv8n và ByteTrack.
    Được thiết kế chuyên biệt cho camera gắn trên tường bao quát lớp học.
    Áp dụng cơ chế thống kê tần suất frame (Statistical Mode + Max Tie-Breaker) trong khoảng thời gian rất ngắn (1s).
    """
    frame_ready = pyqtSignal(QImage, str)       # (QImage, "COUNT")
    count_updated = pyqtSignal(int, str, dict)  # (current_count, delta_text, stats_dict)

    def __init__(self, camera_source, conf_threshold: float = 0.25, imgsz: int = 1280, model_name: str = "models/yolov8n_scuthead.pt", tracker_cfg: str = None, parent=None):
        super().__init__(parent)
        self.camera_source = camera_source
        self.camera_type = "COUNT"
        self.running = False
        self.skip_frames = 2
        self.conf_threshold = float(conf_threshold)
        self.imgsz = int(imgsz)
        self.model_name = str(model_name)
        if tracker_cfg is None:
            default_yaml = os.path.join(os.path.dirname(os.path.abspath(__file__)), "configs", "bytetrack_classroom_062.yaml")
            top2_yaml = os.path.join(os.path.dirname(os.path.abspath(__file__)), "configs", "fasttrack_classroom_075.yaml")
            if os.path.exists(default_yaml):
                self.tracker_cfg = default_yaml
            elif os.path.exists(top2_yaml):
                self.tracker_cfg = top2_yaml
            else:
                self.tracker_cfg = "fasttrack.yaml"
        else:
            self.tracker_cfg = str(tracker_cfg)
        self.current_fps = 0.0
        self.current_infer_ms = 0.0
        self.latest_raw_frame = None
        self.pause_attendance = False

        # State tracking đếm người
        self.active_tracks = {}       # {track_id: {"first_seen": idx, "last_seen": idx, "confirmed": bool}}
        self.confirmed_tracks = set() # Set các track_id hợp lệ đang hiện diện trong phòng
        self.lost_threshold = 45      # Mất dấu > 45 frame (~2.0-2.5s) mới trừ (-1) để chống che khuất và cúi đầu
        self.min_confirm_frames = 3   # Thấy liên tiếp >= 3 frame mới cộng (+1) để chống false positive
        self.recent_delta_text = "Ổn định"
        self.recent_delta_time = 0.0
        self.total_entered = 0
        self.total_exited = 0
        self.last_stable_count = None

        # Cơ chế đếm số người bằng tần suất frame nhiều nhất trong cửa sổ thời gian ngắn (1.0 giây)
        self.window_duration_sec = 1.0  # Khoảng thời gian rất ngắn (1.0 giây)
        self.history_window = collections.deque()  # Lưu (timestamp, raw_count)
        self.history_counts = collections.deque(maxlen=30)  # Lưu các count gần nhất
        self.yolo_model = None

    def get_latest_frame(self):
        """Lấy khung hình thô mới nhất an toàn."""
        if self.latest_raw_frame is not None:
            return self.latest_raw_frame.copy()
        return None

    def reset_counts(self):
        """Đặt lại toàn bộ bộ đếm và trạng thái tracking về 0."""
        self.active_tracks.clear()
        self.confirmed_tracks.clear()
        self.history_window.clear()
        self.history_counts.clear()
        self.total_entered = 0
        self.total_exited = 0
        self.last_stable_count = None
        self.recent_delta_text = "Ổn định"
        self.recent_delta_time = 0.0

        if self.yolo_model is not None and hasattr(self.yolo_model, "predictor") and self.yolo_model.predictor is not None:
            try:
                if hasattr(self.yolo_model.predictor, "trackers"):
                    for trk in self.yolo_model.predictor.trackers:
                        if hasattr(trk, "reset"):
                            trk.reset()
            except Exception:
                pass
        stats = {
            "count": 0,
            "raw_count": 0,
            "delta": "Ổn định",
            "entered": 0,
            "exited": 0,
            "fps": 0.0,
            "infer_ms": 0.0
        }
        self.count_updated.emit(0, "Ổn định", stats)

    def set_source(self, new_source):
        self.camera_source = new_source
        self.reset_counts()

    def stop(self):
        self.running = False
        self.current_fps = 0.0
        self.current_infer_ms = 0.0
        self.wait(2000)

    def _ensure_model(self):
        if self.yolo_model is None:
            from ultralytics import YOLO
            target_name = self.model_name or "models/yolov8n_scuthead.pt"
            candidates = [
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", os.path.basename(target_name)),
                os.path.join(os.path.dirname(os.path.abspath(__file__)), target_name),
                target_name
            ]
            weights_path = next((p for p in candidates if os.path.exists(p)), target_name)
            self.yolo_model = YOLO(weights_path)
        return self.yolo_model


    def run(self):
        self.running = True
        try:
            self._ensure_model()
        except Exception as e:
            print(f"Lỗi khởi tạo YOLO trong PersonCountWorker: {e}")

        self.reset_counts()

        current_src = self.camera_source
        src = current_src
        if isinstance(src, str):
            src = src.strip()
            if src.isdigit():
                src = int(src)

        cap = _safe_open_capture(src)
        frame_idx = 0
        last_boxes_data = []
        last_retry_time = 0
        prev_fps_time = time.perf_counter()
        read_fail_count = 0

        while self.running:
            if self.camera_source != current_src:
                current_src = self.camera_source
                src = current_src
                if isinstance(src, str):
                    src = src.strip()
                    if src.isdigit():
                        src = int(src)
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
                cap = _safe_open_capture(src)
                self.reset_counts()
                last_boxes_data.clear()
                read_fail_count = 0

            if cap is None or not cap.isOpened():
                self.current_fps = 0.0
                self.current_infer_ms = 0.0
                # Tạo khung hình 16:9 thông báo lỗi không có tín hiệu camera
                blank = np.zeros((360, 640, 3), dtype=np.uint8)
                blank[:] = (22, 22, 26)  # Nền xám đen kỹ thuật
                cv2.rectangle(blank, (6, 6), (633, 353), (0, 0, 220), 2)
                cv2.rectangle(blank, (10, 10), (629, 349), (0, 0, 100), 1)

                cv2.putText(blank, "KHONG CO TIN HIEU CAMERA (MAT KET NOI)", (35, 120),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 255), 2)
                cv2.putText(blank, "Camera Gan Tuong: Dem si so lop hoc", (35, 165),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 220, 255), 1)
                src_disp = str(self.camera_source).strip() if (self.camera_source is not None and str(self.camera_source).strip()) else "Chua cau hinh"
                cv2.putText(blank, f"Nguon cau hinh: {src_disp}", (35, 205),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (220, 220, 220), 1)
                cv2.putText(blank, "He thong dang thu ket noi lai... Vui long kiem tra nguon video / IP / thiet bi.", (35, 245),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.42, (160, 160, 160), 1)
                self._emit_frame(blank)

                now_t = time.time()
                if now_t - last_retry_time > 3.0:
                    last_retry_time = now_t
                    cap = _safe_open_capture(src)
                time.sleep(0.5)
                continue

            ret, frame = cap.read()
            if not ret or frame is None:
                if isinstance(src, str) and not src.isdigit() and cap is not None and not src.startswith("http") and not src.startswith("rtsp"):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ret, frame = cap.read()
                if not ret or frame is None:
                    read_fail_count += 1
                if read_fail_count >= 30:
                    try:
                        cap.release()
                    except Exception:
                        pass
                    cap = None
                    read_fail_count = 0
                time.sleep(0.05)
                continue
            read_fail_count = 0

            # Lật ngang nếu nguồn là webcam vật lý để loại bỏ hiệu ứng gương ngược hướng
            if isinstance(src, int) or (isinstance(src, str) and src.strip().isdigit()):
                frame = cv2.flip(frame, 1)

            # Đo đạc FPS (làm mượt EMA)
            now_perf = time.perf_counter()
            dt = now_perf - prev_fps_time
            prev_fps_time = now_perf
            if dt > 0:
                inst_fps = 1.0 / dt
                self.current_fps = 0.9 * self.current_fps + 0.1 * inst_fps if self.current_fps > 0 else inst_fps

            self.latest_raw_frame = frame
            frame_idx += 1

            # Suy luận AI mỗi skip_frames
            if frame_idx % self.skip_frames == 0 and self.yolo_model is not None:
                t_infer_start = time.perf_counter()
                try:
                    # Chạy tracking YOLO với ByteTrack/FastTrack, chỉ phát hiện người (classes=[0])
                    if "062" in str(self.tracker_cfg):
                        infer_conf = min(self.conf_threshold, 0.08)
                    elif "075" in str(self.tracker_cfg) or "fasttrack" in str(self.tracker_cfg).lower():
                        infer_conf = min(self.conf_threshold, 0.10)
                    else:
                        infer_conf = self.conf_threshold
                    results = self.yolo_model.track(
                        frame,
                        persist=True,
                        classes=[0],
                        conf=infer_conf,
                        imgsz=self.imgsz,
                        verbose=False,
                        tracker=self.tracker_cfg
                    )

                    current_frame_track_ids = set()
                    boxes_data = []

                    if results and len(results) > 0 and results[0].boxes is not None:
                        boxes = results[0].boxes
                        for b in boxes:
                            tid = int(b.id[0]) if (b.id is not None and len(b.id) > 0) else None
                            xyxy = b.xyxy[0].cpu().numpy().astype(int)
                            conf = float(b.conf[0])
                            boxes_data.append({"id": tid, "bbox": xyxy, "conf": conf})
                            if tid is not None:
                                current_frame_track_ids.add(tid)

                    last_boxes_data = boxes_data

                    # Cập nhật state máy trạng thái (Cộng +1 / Trừ -1)
                    now_ts = time.time()
                    for tid in current_frame_track_ids:
                        if tid not in self.active_tracks:
                            self.active_tracks[tid] = {
                                "first_seen": frame_idx,
                                "last_seen": frame_idx,
                                "confirmed": False
                            }
                        else:
                            self.active_tracks[tid]["last_seen"] = frame_idx

                        # Kiểm tra xác nhận sinh viên mới vào (+1)
                        track_info = self.active_tracks[tid]
                        if not track_info["confirmed"]:
                            if (frame_idx - track_info["first_seen"] + 1) >= self.min_confirm_frames:
                                track_info["confirmed"] = True
                                self.confirmed_tracks.add(tid)
                                self.total_entered += 1
                                self.recent_delta_text = "+1 sinh viên vừa vào"
                                self.recent_delta_time = now_ts

                    # Kiểm tra sinh viên rời lớp (-1)
                    for tid in list(self.active_tracks.keys()):
                        if tid not in current_frame_track_ids:
                            if (frame_idx - self.active_tracks[tid]["last_seen"]) > self.lost_threshold:
                                if self.active_tracks[tid]["confirmed"]:
                                    self.confirmed_tracks.discard(tid)
                                    self.total_exited += 1
                                    self.recent_delta_text = "-1 sinh viên vừa ra"
                                    self.recent_delta_time = now_ts
                                del self.active_tracks[tid]

                    # Hết thời gian hiển thị delta (sau 3.5s thì chuyển về 'Ổn định')
                    if self.recent_delta_time > 0 and (now_ts - self.recent_delta_time > 3.5):
                        self.recent_delta_text = "Ổn định"

                    # Tính toán số lượng người xuất hiện trong frame hiện tại
                    raw_frame_count = len(boxes_data)
                    self.history_window.append((now_ts, raw_frame_count))

                    # Giữ lại các frame trong cửa sổ thời gian ngắn (1.0 giây)
                    while len(self.history_window) > 1 and (now_ts - self.history_window[0][0]) > self.window_duration_sec:
                        self.history_window.popleft()

                    counts_in_window = [c for t, c in self.history_window]
                    cur_count = compute_modal_headcount(counts_in_window)
                    self.history_counts.append(cur_count)

                    # Cập nhật biến động khi sĩ số theo tần suất frame thay đổi
                    if self.last_stable_count is None:
                        self.last_stable_count = cur_count
                    elif cur_count > self.last_stable_count:
                        diff = cur_count - self.last_stable_count
                        self.total_entered += diff
                        self.recent_delta_text = f"+{diff} sinh viên vừa vào"
                        self.recent_delta_time = now_ts
                        self.last_stable_count = cur_count
                    elif cur_count < self.last_stable_count:
                        diff = self.last_stable_count - cur_count
                        self.total_exited += diff
                        self.recent_delta_text = f"-{diff} sinh viên vừa ra"
                        self.recent_delta_time = now_ts
                        self.last_stable_count = cur_count

                    stats = {
                        "count": cur_count,
                        "raw_count": raw_frame_count,
                        "delta": self.recent_delta_text,
                        "entered": self.total_entered,
                        "exited": self.total_exited,
                        "fps": self.current_fps,
                        "infer_ms": self.current_infer_ms
                    }
                    self.count_updated.emit(cur_count, self.recent_delta_text, stats)

                except Exception as ex:
                    print(f"Lỗi suy luận PersonCountWorker: {ex}")
                finally:
                    infer_time = (time.perf_counter() - t_infer_start) * 1000.0
                    self.current_infer_ms = 0.85 * self.current_infer_ms + 0.15 * infer_time if self.current_infer_ms > 0 else infer_time

            # Vẽ bounding box và telemetry lên khung hình
            annotated = frame.copy()
            for item in last_boxes_data:

                x1, y1, x2, y2 = item["bbox"]
                tid = item["id"]
                conf = item["conf"]
                color = (0, 200, 100) if (tid in self.confirmed_tracks) else (200, 180, 0)
                cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
                lbl_text = f"SV #{tid} ({conf:.0%})" if tid is not None else f"SV ({conf:.0%})"
                cv2.putText(annotated, lbl_text, (x1, max(18, y1 - 6)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
            self._emit_frame(annotated)
            time.sleep(0.02)

        if cap is not None:
            cap.release()

    def _emit_frame(self, frame_bgr):
        self.frame_ready.emit(bgr_to_qimage(frame_bgr), self.camera_type)
