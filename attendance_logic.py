"""
Module xử lý logic nghiệp vụ điểm danh thời gian thực và quản lý trạng thái.
"""
import time
from datetime import datetime, date, time as dt_time

class AttendanceLogic:
    def __init__(self, db_manager, cooldown_seconds: int = 60):
        self.db = db_manager
        self.cooldown_seconds = cooldown_seconds
        # Quản lý thời gian quét gần nhất: { (student_id, camera_type): timestamp }
        self.last_scan_times = {}
        # Quản lý số frame liên tiếp thấy người lạ: { camera_type: count }
        self.stranger_frame_counts = {"IN": 0, "OUT": 0}
        self.is_debug = False
        self.allow_multiple_debug_scans = False
        try:
            self.allow_multiple_debug_scans = self.db.get_setting("debug_allow_multiple_scans", "1") == "1"
        except Exception:
            pass

    def set_debug_mode(self, enabled: bool):
        """Kích hoạt hoặc hủy kích hoạt chế độ Debug và làm sạch bộ đếm cooldown."""
        self.is_debug = enabled
        try:
            self.allow_multiple_debug_scans = self.db.get_setting("debug_allow_multiple_scans", "1") == "1"
        except Exception:
            pass
        self.reset_cooldowns()

    def reset_cooldowns(self):
        """Làm sạch bộ đệm cooldown để kiểm thử tức thì."""
        self.last_scan_times.clear()
        self.stranger_frame_counts = {"IN": 0, "OUT": 0}

    def _parse_time(self, time_str: str):
        """Chuyển chuỗi 'HH:MM' hoặc 'HH:MM:SS' thành datetime.time."""
        parts = [int(p) for p in time_str.split(":")[:2]]
        return datetime.strptime(f"{parts[0]:02d}:{parts[1]:02d}", "%H:%M").time()

    def _check_cooldown(self, key, now_ts: float) -> bool:
        """Kiểm tra và cập nhật cooldown. Trả về True nếu còn trong cooldown, False nếu hợp lệ."""
        if len(self.last_scan_times) > 100:
            self.last_scan_times = {k: v for k, v in self.last_scan_times.items() if now_ts - v < self.cooldown_seconds * 2}
        last_time = self.last_scan_times.get(key, 0)
        if now_ts - last_time < self.cooldown_seconds:
            return True
        self.last_scan_times[key] = now_ts
        return False

    def get_active_session(self, now_time=None) -> dict | None:
        """
        Tìm ca học phù hợp nhất với mốc thời gian hiện tại:
        1. Nếu trước giờ mở ca đầu tiên: lấy ca đầu tiên (để báo Chưa tới giờ mở cổng)
        2. Trong khoảng từ checkin_open của ca i đến checkin_open của ca i+1: thuộc về ca i
        3. Sau giờ mở của ca cuối cùng: thuộc về ca cuối cùng
        """
        if now_time is None:
            now_time = datetime.now().time()
        sessions = self.db.get_all_sessions(only_active=True)
        if not sessions:
            return {
                "id": 1,
                "name": "Ca mặc định",
                "checkin_open": self.db.get_setting("checkin_open", "06:30"),
                "class_start": self.db.get_setting("class_start", "07:30"),
                "late_threshold": self.db.get_setting("late_threshold", "07:45"),
                "checkin_cutoff": self.db.get_setting("checkin_cutoff", "08:15"),
                "early_threshold": self.db.get_setting("early_threshold", "11:15"),
                "class_end": self.db.get_setting("class_end", "11:30")
            }

        sorted_sessions = sorted(sessions, key=lambda s: self._parse_time(s["checkin_open"]))

        # Nếu trước giờ mở ca đầu tiên
        first_open = self._parse_time(sorted_sessions[0]["checkin_open"])
        if now_time < first_open:
            return sorted_sessions[0]

        # Phân định giữa các ca liên tiếp
        for i in range(len(sorted_sessions) - 1):
            curr_open = self._parse_time(sorted_sessions[i]["checkin_open"])
            curr_end = self._parse_time(sorted_sessions[i]["class_end"])
            next_open = self._parse_time(sorted_sessions[i + 1]["checkin_open"])

            # Điểm chuyển giao giữa ca i và ca i+1 (ưu tiên mốc transition_cutoff cấu hình)
            if sorted_sessions[i].get("transition_cutoff"):
                split_time = self._parse_time(sorted_sessions[i]["transition_cutoff"])
            elif curr_end < next_open:
                m_end = curr_end.hour * 60 + curr_end.minute
                m_next = next_open.hour * 60 + next_open.minute
                mid_min = (m_end + m_next) // 2
                split_time = dt_time(mid_min // 60, mid_min % 60)
            else:
                split_time = next_open

            if curr_open <= now_time < split_time:
                return sorted_sessions[i]
            elif split_time <= now_time < next_open:
                return sorted_sessions[i + 1]

        return sorted_sessions[-1]

    def _get_next_session(self, current_session: dict) -> dict | None:
        """Tìm ca học kế tiếp trong ngày theo giờ mở cổng."""
        sessions = self.db.get_all_sessions(only_active=True)
        if not sessions or not current_session:
            return None
        sorted_sessions = sorted(sessions, key=lambda s: self._parse_time(s["checkin_open"]))
        curr_open = self._parse_time(current_session["checkin_open"])
        for s in sorted_sessions:
            if self._parse_time(s["checkin_open"]) > curr_open:
                return s
        return None

    def process_check_in(self, student: dict, current_dt: datetime = None, session_id: int = None) -> tuple[bool, str, str]:
        """
        Xử lý sự kiện nhận diện tại Cổng Vào (Camera 1).
        Trả về: (is_success, status_code, message)
        """
        if current_dt is None:
            current_dt = datetime.now()

        student_id = student["id"]
        today_str = current_dt.date().isoformat()
        current_time_str = current_dt.strftime("%H:%M:%S")
        now_time = current_dt.time()

        # Xác định ca học
        session = None
        if session_id is not None:
            session = self.db.get_session(session_id)
        if session is None:
            session = self.get_active_session(now_time)

        sess_id = session["id"] if session else 1
        sess_name = session["name"] if session else "Ca học"

        # 1. Kiểm tra Cooldown (bỏ qua khi đang ở chế độ Debug)
        if not self.is_debug:
            now_ts = time.time()
            if self._check_cooldown((student_id, "IN"), now_ts):
                return False, "COOLDOWN", "Đã điểm danh, vui lòng không đứng trước camera"

        # 2. Đọc cấu hình giờ ca học
        open_thresh = self._parse_time(session["checkin_open"])
        late_thresh = self._parse_time(session["late_threshold"])
        cutoff_thresh = self._parse_time(session["checkin_cutoff"])
        end_thresh = self._parse_time(session["class_end"])

        # 3. Phân loại theo mốc giờ
        # Khung giờ trước khi mở cổng Check-in
        if now_time < open_thresh:
            self.last_scan_times.pop((student_id, "IN"), None)
            return False, "TOO_EARLY", f"Chưa đến giờ mở cổng điểm danh (mở lúc {open_thresh.strftime('%H:%M')})"

        # Khung giờ sau khi ca học đã kết thúc buổi học (quá class_end)
        if now_time > end_thresh:
            self.last_scan_times.pop((student_id, "IN"), None)
            next_sess = self._get_next_session(session)
            if next_sess:
                next_open = self._parse_time(next_sess["checkin_open"])
                return False, "TOO_EARLY", f"Chưa đến giờ mở cổng điểm danh {next_sess['name']} (mở lúc {next_open.strftime('%H:%M')})"
            return False, "CLASS_ENDED", f"Ca học {sess_name} đã kết thúc"

        # 4. Kiểm tra bản ghi trong ngày cho ca học này (khi ca đang mở)
        rec = self.db.get_today_attendance(student_id, today_str, session_id=sess_id)
        if rec and rec.get("check_in_time"):
            if not (self.is_debug and self.allow_multiple_debug_scans):
                return False, "ALREADY_CHECKED_IN", f"Đã điểm danh {sess_name} vào lúc {rec['check_in_time']}"

        if now_time > cutoff_thresh:
            # Quá giờ đóng cổng trong buổi học -> Ghi nhận vắng
            self.db.record_check_in(student_id, current_time_str, "VANG", today_str, session_id=sess_id)
            return False, "VANG", f"Quá giờ đóng cổng {sess_name}. Hệ thống ghi nhận Vắng"

        if now_time <= late_thresh:
            status = "DUNG_GIO"
            msg = "Điểm danh thành công: Đúng giờ"
        else:
            status = "DI_TRE"
            msg = "Điểm danh thành công: Đi trễ"

        self.db.record_check_in(student_id, current_time_str, status, today_str, session_id=sess_id)
        return True, status, msg

    def process_check_out(self, student: dict, current_dt: datetime = None, session_id: int = None) -> tuple[bool, str, str]:
        """
        Xử lý sự kiện nhận diện tại Cổng Ra (Camera 2).
        Trả về: (is_success, status_code, message)
        """
        if current_dt is None:
            current_dt = datetime.now()

        student_id = student["id"]
        today_str = current_dt.date().isoformat()
        current_time_str = current_dt.strftime("%H:%M:%S")
        now_time = current_dt.time()

        # Xác định ca học
        session = None
        if session_id is not None:
            session = self.db.get_session(session_id)
        if session is None:
            # Ưu tiên tìm ca mà sinh viên này đã check-in nhưng chưa check-out trong ngày
            tbl = self.db._get_attendance_table()
            try:
                with self.db._get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute(f"""
                    SELECT session_id FROM {tbl}
                    WHERE student_id = ? AND date = ? AND check_in_time IS NOT NULL AND check_out_time IS NULL
                    ORDER BY id DESC LIMIT 1
                    """, (student_id, today_str))
                    row = cur.fetchone()
                    if row and row["session_id"]:
                        session = self.db.get_session(row["session_id"])
            except Exception:
                pass

        if session is None:
            session = self.get_active_session(now_time)

        sess_id = session["id"] if session else 1
        sess_name = session["name"] if session else "Ca học"

        # 1. Kiểm tra Cooldown (bỏ qua khi đang ở chế độ Debug)
        if not self.is_debug:
            now_ts = time.time()
            if self._check_cooldown((student_id, "OUT"), now_ts):
                return False, "COOLDOWN", "Vừa check-out, vui lòng di chuyển"

        # 2. Kiểm tra bản ghi trong ngày cho ca học này
        rec = self.db.get_today_attendance(student_id, today_str, session_id=sess_id)
        if not rec or not rec.get("check_in_time"):
            self.last_scan_times.pop((student_id, "OUT"), None)
            return False, "NO_CHECK_IN", "Chưa có bản ghi điểm danh Cổng Vào hôm nay"

        # Nếu sinh viên đã bị ghi nhận VẮNG, không cho phép check-out xóa điểm vắng
        if rec.get("status") == "VANG" and not (self.is_debug and self.allow_multiple_debug_scans):
            self.last_scan_times.pop((student_id, "OUT"), None)
            return False, "ABSENT", "Sinh viên đã bị ghi nhận Vắng trong ca học này, không thể check-out"

        if rec.get("check_out_time"):
            if not (self.is_debug and self.allow_multiple_debug_scans):
                return False, "ALREADY_CHECKED_OUT", f"Đã check-out lúc {rec['check_out_time']}"

        # 3. Đọc cấu hình giờ ca học
        early_thresh = self._parse_time(session["early_threshold"])

        # Kiểm tra nếu ca học đã quá thời gian kết thúc (vượt quá transition_cutoff)
        trans_cutoff = None
        if session.get("transition_cutoff"):
            trans_cutoff = self._parse_time(session["transition_cutoff"])
        elif session.get("class_end"):
            end_t = self._parse_time(session["class_end"])
            m = min(23 * 60 + 59, end_t.hour * 60 + end_t.minute + 60)
            trans_cutoff = dt_time(m // 60, m % 60)

        if trans_cutoff and now_time > trans_cutoff and not (self.is_debug and self.allow_multiple_debug_scans):
            self.last_scan_times.pop((student_id, "OUT"), None)
            return False, "CLASS_ENDED", f"Đã quá thời gian kết thúc ca học {sess_name}"

        # 4. Phân loại trạng thái (Bảo lưu trạng thái Đi trễ / Vắng mặt, không tính thành Đúng giờ)
        prev_status = rec.get("status")
        if prev_status == "VANG":
            status = "VANG"
            msg = "Check-out ghi nhận: Sinh viên vắng mặt"
        elif prev_status == "DI_TRE":
            if now_time < early_thresh:
                status = "VE_SOM"
                msg = "Check-out ghi nhận: Đi trễ & Về sớm"
            else:
                status = "DI_TRE"
                msg = "Check-out thành công: Đi trễ (Đã hoàn thành buổi học)"
        else:
            if now_time < early_thresh:
                status = "VE_SOM"
                msg = "Check-out ghi nhận: Về sớm"
            else:
                status = "HOAN_THANH"
                msg = "Check-out thành công: Hoàn thành buổi học"

        self.db.record_check_out(student_id, current_time_str, status, today_str, session_id=sess_id)
        return True, status, msg

    def handle_stranger(self, camera_type: str) -> bool:
        """
        Đếm frame liên tiếp xuất hiện người lạ. Nếu liên tục > 10 frame -> kích hoạt cảnh báo.
        """
        self.stranger_frame_counts[camera_type] += 1
        if self.stranger_frame_counts[camera_type] == 10:
            self.db.log_alert(camera_type, f"Phát hiện người chưa đăng ký tại Cổng {camera_type}")
            return True
        return False

    def reset_stranger(self, camera_type: str):
        self.stranger_frame_counts[camera_type] = 0


def _format_schedule_time(time_str: str, use_ampm: bool = False) -> str:
    """Định dạng chuỗi giờ (HH:mm) sang 12h (hh:mm AP) nếu use_ampm=True."""
    if not time_str or not use_ampm:
        return time_str or ""
    try:
        parts = str(time_str).strip().split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        ap = "AM" if h < 12 else "PM"
        h12 = h % 12
        if h12 == 0:
            h12 = 12
        return f"{h12:02d}:{m:02d} {ap}"
    except Exception:
        return time_str


def validate_schedule_chronology(sessions: list[dict], use_ampm: bool = False) -> str | None:
    """
    Kiểm tra tính logic thực tế của danh sách ca học.
    Thứ tự logic bắt buộc cho từng ca học:
    checkin_open < class_start <= late_threshold <= checkin_cutoff <= early_threshold <= class_end <= transition_cutoff

    Thứ tự logic bắt buộc giữa các ca học liên tiếp (nếu có):
    - Ca sau phải mở cổng sau ca trước (curr_open < next_open)
    - Ca trước phải kết thúc trước khi ca sau mở cổng (curr_end <= next_open)
    - Giờ chuyển ca trước không được muộn hơn giờ mở cổng ca sau (curr_trans <= next_open)

    Trả về chuỗi thông báo lỗi tiếng Việt nếu vi phạm, hoặc None nếu hoàn toàn hợp lệ.
    Hỗ trợ định dạng hiển thị giờ 12h (AM/PM) khi use_ampm=True.
    """
    if not sessions:
        return "Hệ thống phải có ít nhất 1 ca học."

    _fmt = lambda val: _format_schedule_time(val, use_ampm)

    def _to_min(val: str, field_desc: str) -> int:
        try:
            parts = str(val).strip().split(":")
            h, m = int(parts[0]), int(parts[1])
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError()
            return h * 60 + m
        except Exception:
            raise ValueError(f"Định dạng {field_desc} '{val}' không hợp lệ (phải là HH:mm).")

    parsed = []
    for idx, s in enumerate(sessions):
        name = s.get("name") or f"Ca {idx + 1}"
        try:
            m_open = _to_min(s.get("checkin_open", ""), f"Giờ mở cổng của '{name}'")
            m_start = _to_min(s.get("class_start", ""), f"Giờ bắt đầu của '{name}'")
            m_late = _to_min(s.get("late_threshold", ""), f"Hạn chót đúng giờ của '{name}'")
            m_cutoff = _to_min(s.get("checkin_cutoff", ""), f"Giờ đóng cổng của '{name}'")
            m_early = _to_min(s.get("early_threshold", ""), f"Hạn chót về sớm của '{name}'")
            m_end = _to_min(s.get("class_end", ""), f"Giờ kết thúc của '{name}'")
            m_trans = _to_min(s.get("transition_cutoff", s.get("class_end", "")), f"Giờ chuyển ca của '{name}'")
        except ValueError as e:
            return str(e)

        # 1. Kiểm tra logic trong cùng một ca học (Intra-session)
        if m_open >= m_start:
            return f"Ca học '{name}': Giờ mở cổng ({_fmt(s.get('checkin_open'))}) phải sớm hơn giờ bắt đầu ca học ({_fmt(s.get('class_start'))})."

        if m_start > m_late:
            return f"Ca học '{name}': Hạn chót tính đúng giờ ({_fmt(s.get('late_threshold'))}) không được sớm hơn giờ bắt đầu ca học ({_fmt(s.get('class_start'))})."

        if m_late > m_cutoff:
            return f"Ca học '{name}': Giờ đóng cổng tính vắng ({_fmt(s.get('checkin_cutoff'))}) không được sớm hơn hạn chót đúng giờ ({_fmt(s.get('late_threshold'))})."

        if m_cutoff > m_early:
            return f"Ca học '{name}': Hạn chót tính về sớm ({_fmt(s.get('early_threshold'))}) không được sớm hơn giờ đóng cổng ({_fmt(s.get('checkin_cutoff'))})."

        if m_early > m_end:
            return f"Ca học '{name}': Giờ kết thúc buổi học ({_fmt(s.get('class_end'))}) không được sớm hơn hạn chót tính về sớm ({_fmt(s.get('early_threshold'))})."

        if m_end > m_trans:
            return f"Ca học '{name}': Giờ chuyển sang ca sau ({_fmt(s.get('transition_cutoff'))}) không được sớm hơn giờ kết thúc buổi học ({_fmt(s.get('class_end'))})."

        parsed.append({
            "name": name,
            "raw": s,
            "open": m_open,
            "start": m_start,
            "late": m_late,
            "cutoff": m_cutoff,
            "early": m_early,
            "end": m_end,
            "trans": m_trans
        })

    # 2. Kiểm tra logic giữa các ca học liên tiếp (Inter-session)
    for i in range(len(parsed) - 1):
        curr_p = parsed[i]
        next_p = parsed[i + 1]

        if curr_p["open"] >= next_p["open"]:
            return f"Thứ tự ca học không hợp lệ: '{next_p['name']}' (mở cổng lúc {_fmt(next_p['raw'].get('checkin_open'))}) không được mở trước hoặc cùng lúc với '{curr_p['name']}' (mở cổng lúc {_fmt(curr_p['raw'].get('checkin_open'))})."

        if curr_p["end"] > next_p["open"]:
            return f"'{curr_p['name']}' kết thúc ({_fmt(curr_p['raw'].get('class_end'))}) sau khi '{next_p['name']}' mở cổng ({_fmt(next_p['raw'].get('checkin_open'))}). Các ca học không được chồng lấn nhau."

        if curr_p["trans"] > next_p["open"]:
            return f"Giờ chuyển ca của '{curr_p['name']}' ({_fmt(curr_p['raw'].get('transition_cutoff'))}) không được muộn hơn giờ mở cổng của '{next_p['name']}' ({_fmt(next_p['raw'].get('checkin_open'))})."

    return None

