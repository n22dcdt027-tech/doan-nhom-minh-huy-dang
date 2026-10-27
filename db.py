"""
Module quản lý Cơ sở dữ liệu SQLite và Mã hóa Sinh trắc học (AES-GCM).
"""
import os
import sqlite3
import hashlib
import secrets
import cv2
import numpy as np
from datetime import datetime, date, timedelta
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Khóa AES-256 cố định cục bộ (hoặc dẫn xuất từ admin key)
# shortcut: hardcoded local salt key, thay bằng PBKDF2 từ mật khẩu admin nếu cần đa người dùng
LOCAL_SECRET_KEY = b"UniFaceAttendanceSecretKey2026!#"[:32]

class DatabaseManager:
    def __init__(self, db_path="data/attendance.db"):
        self.db_path = db_path
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self.aes = AESGCM(LOCAL_SECRET_KEY)
        self.is_debug = False
        self.reload_cache_cb = None
        self._init_db()

    def set_debug_mode(self, enabled: bool):
        """Bật/tắt chế độ sử dụng bảng điểm danh thử nghiệm độc lập."""
        self.is_debug = enabled

    def _get_attendance_table(self) -> str:
        return "attendance_debug" if self.is_debug else "attendance"

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL;")
            cur.execute("""
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_code TEXT NOT NULL,
                full_name TEXT NOT NULL,
                class_name TEXT,
                encrypted_embedding BLOB NOT NULL,
                photo_path TEXT,
                is_active INTEGER DEFAULT 1,
                is_mock INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(student_code, is_mock)
            )
            """)
            # Migration bổ sung cột is_mock nếu bảng cũ chưa có
            cur.execute("PRAGMA table_info(students)")
            stu_cols = [r[1] for r in cur.fetchall()]
            if stu_cols and "is_mock" not in stu_cols:
                cur.execute("ALTER TABLE students ADD COLUMN is_mock INTEGER DEFAULT 0")

            # Migration nâng cấp ràng buộc thành UNIQUE(student_code, is_mock) để MSSV debug độc lập với bản chính
            cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='students'")
            master_row = cur.fetchone()
            if master_row and master_row[0] and "UNIQUE(student_code, is_mock)" not in master_row[0]:
                try:
                    cur.execute("""
                    CREATE TABLE students_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        student_code TEXT NOT NULL,
                        full_name TEXT NOT NULL,
                        class_name TEXT,
                        encrypted_embedding BLOB NOT NULL,
                        photo_path TEXT,
                        is_active INTEGER DEFAULT 1,
                        is_mock INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(student_code, is_mock)
                    )
                    """)
                    cur.execute("""
                    INSERT INTO students_new (id, student_code, full_name, class_name, encrypted_embedding, photo_path, is_active, is_mock, created_at)
                    SELECT id, student_code, full_name, class_name, encrypted_embedding, photo_path, is_active, is_mock, created_at FROM students
                    """)
                    cur.execute("DROP TABLE students")
                    cur.execute("ALTER TABLE students_new RENAME TO students")
                except Exception as e:
                    print(f"Lỗi migration students composite unique: {e}")

            # Bảng danh mục lớp học chính thức
            cur.execute("""
            CREATE TABLE IF NOT EXISTS classes (
                id INTEGER NOT NULL,
                name TEXT NOT NULL,
                is_mock INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id, is_mock),
                UNIQUE (name, is_mock)
            )
            """)
            # Migration bổ sung cột is_mock nếu bảng cũ chưa có
            cur.execute("PRAGMA table_info(classes)")
            cls_cols = [r[1] for r in cur.fetchall()]
            if cls_cols and "is_mock" not in cls_cols:
                cur.execute("ALTER TABLE classes ADD COLUMN is_mock INTEGER DEFAULT 0")

            # Migration nâng cấp ràng buộc classes thành PRIMARY KEY (id, is_mock) và UNIQUE(name, is_mock)
            cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='classes'")
            master_cls = cur.fetchone()
            if master_cls and master_cls[0] and "PRIMARY KEY (id, is_mock)" not in master_cls[0]:
                try:
                    cur.execute("""
                    CREATE TABLE classes_new (
                        id INTEGER NOT NULL,
                        name TEXT NOT NULL,
                        is_mock INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (id, is_mock),
                        UNIQUE (name, is_mock)
                    )
                    """)
                    cur.execute("""
                    INSERT INTO classes_new (id, name, is_mock, created_at)
                    SELECT id, name, is_mock, created_at FROM classes
                    """)
                    cur.execute("DROP TABLE classes")
                    cur.execute("ALTER TABLE classes_new RENAME TO classes")
                except Exception as e:
                    print(f"Lỗi migration classes composite primary key: {e}")

            # Migration nạp các lớp đã có từ bảng students vào bảng classes
            cur.execute("""
            INSERT OR IGNORE INTO classes (name, is_mock)
            SELECT DISTINCT trim(class_name), 0 FROM students
            WHERE class_name IS NOT NULL AND trim(class_name) != ''
            """)

            # Bảng phân công lớp giảng dạy cho tài khoản quản trị
            cur.execute("""
            CREATE TABLE IF NOT EXISTS admin_user_classes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                class_name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES admin_users(id) ON DELETE CASCADE,
                UNIQUE(user_id, class_name)
            )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_admin_user_classes_uid ON admin_user_classes(user_id)")

            cur.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                checkin_open TEXT NOT NULL,
                class_start TEXT NOT NULL,
                late_threshold TEXT NOT NULL,
                checkin_cutoff TEXT NOT NULL,
                early_threshold TEXT NOT NULL,
                class_end TEXT NOT NULL,
                transition_cutoff TEXT DEFAULT '12:00',
                is_active INTEGER DEFAULT 1,
                sort_order INTEGER DEFAULT 0
            )
            """)
            # Migration bổ sung cột transition_cutoff nếu bảng cũ chưa có
            cur.execute("PRAGMA table_info(sessions)")
            sess_cols = [r[1] for r in cur.fetchall()]
            if sess_cols and "transition_cutoff" not in sess_cols:
                cur.execute("ALTER TABLE sessions ADD COLUMN transition_cutoff TEXT DEFAULT '12:00'")
                cur.execute("UPDATE sessions SET transition_cutoff = '18:00' WHERE name LIKE '%Chiều%' OR name LIKE '%Ca 2%'")

            # Migration chuẩn hóa tên ca học chỉ gọi theo số (Ca 1, Ca 2)
            cur.execute("UPDATE sessions SET name = 'Ca 1' WHERE name LIKE '%Ca 1%' OR name LIKE '%Sáng%'")
            cur.execute("UPDATE sessions SET name = 'Ca 2' WHERE name LIKE '%Ca 2%' OR name LIKE '%Chiều%'")

            # Khởi tạo ca học mặc định nếu chưa có
            cur.execute("SELECT COUNT(*) FROM sessions")
            if cur.fetchone()[0] == 0:
                cur.execute("""
                INSERT INTO sessions (name, checkin_open, class_start, late_threshold, checkin_cutoff, early_threshold, class_end, transition_cutoff, is_active, sort_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 0)
                """, ("Ca 1", "06:30", "07:30", "07:45", "08:15", "11:15", "11:30", "12:00"))
                cur.execute("""
                INSERT INTO sessions (name, checkin_open, class_start, late_threshold, checkin_cutoff, early_threshold, class_end, transition_cutoff, is_active, sort_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 1)
                """, ("Ca 2", "12:30", "13:30", "13:45", "14:15", "17:15", "17:30", "18:00"))

            # Tạo hoặc migrate bảng attendance và attendance_debug với session_id
            for tbl in ["attendance", "attendance_debug"]:
                cur.execute(f"PRAGMA table_info({tbl})")
                cols = [r[1] for r in cur.fetchall()]
                if not cols:
                    cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS {tbl} (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        student_id INTEGER NOT NULL,
                        session_id INTEGER DEFAULT 1,
                        date TEXT NOT NULL,
                        check_in_time TEXT,
                        check_out_time TEXT,
                        status TEXT NOT NULL,
                        note TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (student_id) REFERENCES students(id),
                        FOREIGN KEY (session_id) REFERENCES sessions(id),
                        UNIQUE(student_id, date, session_id)
                    )
                    """)
                elif "session_id" not in cols:
                    # Migrate từ UNIQUE(student_id, date) sang UNIQUE(student_id, date, session_id)
                    cur.execute(f"""
                    CREATE TABLE {tbl}_migration (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        student_id INTEGER NOT NULL,
                        session_id INTEGER DEFAULT 1,
                        date TEXT NOT NULL,
                        check_in_time TEXT,
                        check_out_time TEXT,
                        status TEXT NOT NULL,
                        note TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (student_id) REFERENCES students(id),
                        FOREIGN KEY (session_id) REFERENCES sessions(id),
                        UNIQUE(student_id, date, session_id)
                    )
                    """)
                    cur.execute(f"""
                    INSERT INTO {tbl}_migration (id, student_id, session_id, date, check_in_time, check_out_time, status, note, created_at)
                    SELECT id, student_id, 1, date, check_in_time, check_out_time, status, note, created_at FROM {tbl}
                    """)
                    cur.execute(f"DROP TABLE {tbl}")
                    cur.execute(f"ALTER TABLE {tbl}_migration RENAME TO {tbl}")

            cur.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                camera_source TEXT,
                snapshot_path TEXT,
                description TEXT
            )
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """)
            # Khởi tạo cài đặt mặc định nếu chưa có
            defaults = [
                ("camera_in", "0"),
                ("camera_out", "1"),
                ("checkin_open", "06:30"),
                ("class_start", "07:30"),
                ("late_threshold", "07:45"),
                ("checkin_cutoff", "08:15"),
                ("early_threshold", "11:15"),
                ("class_end", "11:30"),
                ("transition_cutoff", "12:00"),
                ("face_threshold", "0.55"),
                ("verification_duration", "3.0"),
                ("verification_ratio", "70"),
                ("min_face_size_pct", "18"),
                ("strictness_preset", "standard"),
                ("max_roll_deg", "15"),
                ("max_yaw_pct", "30"),
                ("anti_spoofing_enabled", "0"),
                ("spoof_threshold", "0.85"),
                ("headcount_conf", "0.25"),
                ("failed_attempt_cooldown_mins", "15")
            ]
            for k, v in defaults:
                cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
            # Loại bỏ hoàn toàn bản ghi lưu mật khẩu plaintext nếu còn sót
            cur.execute("DELETE FROM settings WHERE key = 'admin_password'")

            # Bảng quản trị viên hệ thống (Admin Users)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS admin_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL COLLATE NOCASE,
                full_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'super_admin',
                avatar_path TEXT DEFAULT '',
                is_active INTEGER DEFAULT 1,
                failed_attempts INTEGER DEFAULT 0,
                locked_until TEXT,
                last_failed_at TEXT,
                last_login TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)

            # Bảng nhật ký kiểm toán hành vi quản trị (Admin Audit Logs)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS admin_audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_id INTEGER,
                username TEXT NOT NULL,
                action TEXT NOT NULL,
                details TEXT,
                target_entity TEXT
            )
            """)

            # Tự động di trú từ mật khẩu admin cũ nếu chưa có tài khoản nào
            cur.execute("SELECT COUNT(*) FROM admin_users")
            if cur.fetchone()[0] == 0:
                cur.execute("SELECT value FROM settings WHERE key = 'admin_password'")
                row_pwd = cur.fetchone()
                legacy_pwd = row_pwd["value"] if row_pwd else "admin123"
                salt = secrets.token_hex(16)
                dk = hashlib.pbkdf2_hmac('sha256', legacy_pwd.encode('utf-8'), bytes.fromhex(salt), 600000)
                pwd_hash = f"pbkdf2:sha256:600000${salt}${dk.hex()}"
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cur.execute("""
                INSERT INTO admin_users (username, full_name, password_hash, role, avatar_path, is_active, failed_attempts, created_at)
                VALUES (?, ?, ?, 'super_admin', '', 1, 0, ?)
                """, ("admin", "Quản trị viên Hệ thống", pwd_hash, now_str))
                cur.execute("""
                INSERT INTO admin_audit_logs (username, action, details, timestamp)
                VALUES (?, 'MIGRATION', 'Tự động khởi tạo tài khoản super_admin từ mật khẩu cũ', ?)
                """, ("admin", now_str))

            # Migration chuẩn hóa vai trò: chuyển đổi các tài khoản viewer cũ sang teacher
            self._migrate_classes_and_roles(conn)

            # Migration bổ sung cột last_failed_at cho admin_users nếu chưa tồn tại
            cur.execute("PRAGMA table_info(admin_users)")
            admin_cols = [col["name"] for col in cur.fetchall()]
            if "last_failed_at" not in admin_cols:
                cur.execute("ALTER TABLE admin_users ADD COLUMN last_failed_at TEXT")

            conn.commit()
        self._migrate_photos_to_encrypted()

    def _migrate_classes_and_roles(self, conn=None):
        """Migration chuẩn hóa vai trò và đồng bộ danh mục lớp học."""
        def _do_migrate(c):
            cur = c.cursor()
            cur.execute("UPDATE admin_users SET role = 'teacher' WHERE role = 'viewer'")
            cur.execute("SELECT DISTINCT trim(class_name) AS cname FROM students WHERE trim(class_name) != '' AND class_name IS NOT NULL")
            for r in cur.fetchall():
                cname = r["cname"]
                if cname:
                    cur.execute("INSERT OR IGNORE INTO classes (name, is_mock) VALUES (?, 0)", (cname,))

        if conn is not None:
            _do_migrate(conn)
        else:
            with self._get_connection() as conn_local:
                _do_migrate(conn_local)
                conn_local.commit()

    def encrypt_bytes(self, raw_bytes: bytes) -> bytes:
        """Mã hoá dữ liệu byte tuỳ ý bằng AES-256-GCM kèm nonce 12-byte."""
        nonce = os.urandom(12)
        ciphertext = self.aes.encrypt(nonce, raw_bytes, None)
        return nonce + ciphertext

    def decrypt_bytes(self, encrypted_bytes: bytes) -> bytes:
        """Giải mã dữ liệu AES-256-GCM."""
        nonce = encrypted_bytes[:12]
        ciphertext = encrypted_bytes[12:]
        return self.aes.decrypt(nonce, ciphertext, None)

    def encrypt_embedding(self, embedding: np.ndarray) -> bytes:
        """Mã hoá vector 512-dim float32 thành AES-GCM ciphertext kèm nonce."""
        if not isinstance(embedding, np.ndarray):
            embedding = np.array(embedding, dtype=np.float32)
        return self.encrypt_bytes(embedding.astype(np.float32).tobytes())

    def decrypt_embedding(self, encrypted_bytes: bytes) -> np.ndarray:
        """Giải mã AES-GCM ciphertext thành numpy array 512-dim."""
        raw_bytes = self.decrypt_bytes(encrypted_bytes)
        return np.frombuffer(raw_bytes, dtype=np.float32)

    def save_encrypted_photo(self, photo_path: str, image: np.ndarray) -> str:
        """
        Nén ảnh thành JPEG và mã hoá AES-256-GCM trước khi ghi xuống đĩa.
        Đảm bảo định dạng lưu trữ luôn có đuôi .enc.
        """
        if not photo_path.endswith(".enc"):
            base, _ = os.path.splitext(photo_path)
            photo_path = f"{base}.enc"

        dir_name = os.path.dirname(photo_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)

        success, encoded_img = cv2.imencode(".jpg", image)
        if not success:
            raise ValueError("Không thể nén ảnh thành định dạng JPEG để mã hoá.")

        encrypted_bytes = self.encrypt_bytes(encoded_img.tobytes())
        with open(photo_path, "wb") as f:
            f.write(encrypted_bytes)
        return photo_path

    def load_encrypted_photo(self, photo_path: str) -> np.ndarray | None:
        """
        Đọc và giải mã ảnh trực tiếp trong RAM (không tạo file tạm trên ổ đĩa).
        Hỗ trợ cả tệp mã hoá .enc lẫn tệp ảnh thô thông thường (.jpg, .png) để tương thích ngược.
        """
        if not photo_path:
            return None

        # Tự động tìm file .enc nếu truyền vào đường dẫn .jpg cũ
        if not os.path.exists(photo_path):
            base, _ = os.path.splitext(photo_path)
            enc_path = f"{base}.enc"
            if os.path.exists(enc_path):
                photo_path = enc_path
            else:
                return None

        try:
            with open(photo_path, "rb") as f:
                data = f.read()

            if photo_path.endswith(".enc"):
                raw_bytes = self.decrypt_bytes(data)
                return cv2.imdecode(np.frombuffer(raw_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
            else:
                # Thử giải mã nếu dữ liệu là ciphertext, nếu không thì decode thông thường
                try:
                    raw_bytes = self.decrypt_bytes(data)
                    img = cv2.imdecode(np.frombuffer(raw_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
                    if img is not None:
                        return img
                except Exception:
                    pass
                return cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        except Exception:
            return None

    def _migrate_photos_to_encrypted(self):
        """Tự động mã hoá các ảnh học sinh cũ (.jpg/.png) sang tệp .enc và cập nhật CSDL."""
        try:
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT id, photo_path FROM students WHERE photo_path IS NOT NULL AND photo_path != ''")
                rows = cur.fetchall()
                for r in rows:
                    stu_id = r["id"]
                    p_path = r["photo_path"]
                    if not p_path.endswith(".enc") and os.path.exists(p_path):
                        try:
                            with open(p_path, "rb") as f:
                                raw_bytes = f.read()
                            img = cv2.imdecode(np.frombuffer(raw_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
                            if img is None:
                                img = cv2.imread(p_path)
                            if img is not None:
                                new_path = self.save_encrypted_photo(p_path, img)
                                cur.execute("UPDATE students SET photo_path = ? WHERE id = ?", (new_path, stu_id))
                                if os.path.abspath(p_path) != os.path.abspath(new_path) and os.path.exists(p_path):
                                    os.remove(p_path)
                        except Exception:
                            pass

                # Tự động mã hoá ảnh đại diện quản trị viên (.jpg/.png) sang .enc và xóa ảnh cũ
                cur.execute("SELECT id, avatar_path FROM admin_users WHERE avatar_path IS NOT NULL AND avatar_path != ''")
                for r in cur.fetchall():
                    u_id = r["id"]
                    a_path = r["avatar_path"]
                    if not a_path.endswith(".enc") and os.path.exists(a_path):
                        try:
                            with open(a_path, "rb") as f:
                                raw_bytes = f.read()
                            img = cv2.imdecode(np.frombuffer(raw_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
                            if img is None:
                                img = cv2.imread(a_path)
                            if img is not None:
                                new_path = self.save_encrypted_photo(a_path, img)
                                cur.execute("UPDATE admin_users SET avatar_path = ? WHERE id = ?", (new_path, u_id))
                                if os.path.abspath(a_path) != os.path.abspath(new_path) and os.path.exists(a_path):
                                    os.remove(a_path)
                        except Exception:
                            pass
                conn.commit()
        except Exception:
            pass
    def _get_lowest_available_id(self, table_name: str, conn: sqlite3.Connection = None, is_mock: int | None = None) -> int:
        """Tìm số ID nguyên dương nhỏ nhất chưa được sử dụng (lấp đầy khoảng trống ID khi có bản ghi bị xóa)."""
        def _find(c):
            cur = c.cursor()
            if table_name == "classes":
                mock_val = int(is_mock if is_mock is not None else (1 if self.is_debug else 0))
                cur.execute(f"SELECT id FROM {table_name} WHERE is_mock = ? ORDER BY id ASC", (mock_val,))
            else:
                cur.execute(f"SELECT id FROM {table_name} ORDER BY id ASC")
            used_ids = set(r[0] if isinstance(r, tuple) else r["id"] for r in cur.fetchall())
            candidate = 1
            while candidate in used_ids:
                candidate += 1
            return candidate

        if conn is not None:
            return _find(conn)
        with self._get_connection() as local_conn:
            return _find(local_conn)

    def _sync_sqlite_sequence(self, table_name: str, conn: sqlite3.Connection = None):
        """Đồng bộ giá trị sqlite_sequence với MAX(id) thực tế của bảng."""
        def _sync(c):
            cur = c.cursor()
            cur.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table_name}")
            max_id = cur.fetchone()[0]
            if max_id == 0:
                c.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table_name,))
            else:
                cur.execute("SELECT 1 FROM sqlite_sequence WHERE name = ?", (table_name,))
                if cur.fetchone():
                    c.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = ?", (max_id, table_name))
                else:
                    c.execute("INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)", (table_name, max_id))

        if conn is not None:
            _sync(conn)
        else:
            with self._get_connection() as local_conn:
                _sync(local_conn)
                local_conn.commit()

    def add_student(self, student_code: str, full_name: str, class_name: str, embedding: np.ndarray, photo_path: str = "", is_mock: int | None = None) -> int:
        if is_mock is None:
            is_mock = 1 if self.is_debug else 0
        encrypted_emb = self.encrypt_embedding(embedding)
        with self._get_connection() as conn:
            cur = conn.cursor()
            target_id = self._get_lowest_available_id("students", conn)
            cur.execute("""
            INSERT INTO students (id, student_code, full_name, class_name, encrypted_embedding, photo_path, is_mock)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (target_id, student_code, full_name, class_name, encrypted_emb, photo_path, is_mock))
            self._sync_sqlite_sequence("students", conn)
            conn.commit()
            return target_id

    def delete_student(self, student_id: int):
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT photo_path FROM students WHERE id = ?", (student_id,))
            row = cur.fetchone()
            if row and row["photo_path"] and os.path.exists(row["photo_path"]):
                try:
                    os.remove(row["photo_path"])
                except Exception:
                    pass
            conn.execute("DELETE FROM attendance WHERE student_id = ?", (student_id,))
            conn.execute("DELETE FROM attendance_debug WHERE student_id = ?", (student_id,))
            conn.execute("DELETE FROM students WHERE id = ?", (student_id,))
            self._sync_sqlite_sequence("students", conn)
            self._sync_sqlite_sequence("attendance", conn)
            self._sync_sqlite_sequence("attendance_debug", conn)
            conn.commit()

    def update_student(self, student_id: int, student_code: str, full_name: str, class_name: str):
        """Cập nhật thông tin hành chính của sinh viên (MSSV, Họ tên, Lớp)."""
        with self._get_connection() as conn:
            conn.execute("""
            UPDATE students 
            SET student_code = ?, full_name = ?, class_name = ?
            WHERE id = ?
            """, (student_code, full_name, class_name, student_id))
            conn.commit()

    def update_students_batch(self, students: list[dict]):
        """Cập nhật hàng loạt thông tin sinh viên trong 1 transaction duy nhất để tối ưu I/O."""
        if not students:
            return
        with self._get_connection() as conn:
            conn.executemany("""
            UPDATE students 
            SET student_code = ?, full_name = ?, class_name = ?
            WHERE id = ?
            """, [(s["student_code"], s["full_name"], s.get("class_name", ""), s["id"]) for s in students])
            conn.commit()

    def get_student_by_code(self, student_code: str, is_mock: int | str | None = None):
        """Truy vấn sinh viên theo MSSV.
        Nếu is_mock is None: tìm kiếm không phân biệt is_mock để đảm bảo tương thích ngược.
        Nếu is_mock được chỉ định cụ thể (0 hoặc 1), lọc chính xác theo is_mock.
        """
        with self._get_connection() as conn:
            cur = conn.cursor()
            if is_mock is not None and is_mock != -1 and is_mock != "all":
                cur.execute("SELECT id, student_code, full_name, class_name, photo_path, is_mock FROM students WHERE student_code = ? AND is_active = 1 AND is_mock = ?", (student_code, int(is_mock)))
            else:
                cur.execute("SELECT id, student_code, full_name, class_name, photo_path, is_mock FROM students WHERE student_code = ? AND is_active = 1", (student_code,))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_all_active_students(self, class_names: list[str] | None = None, is_mock: int | str | None = None):
        if is_mock is None:
            is_mock = 1 if self.is_debug else 0
        with self._get_connection() as conn:
            cur = conn.cursor()
            conditions = ["is_active = 1"]
            params = []
            if is_mock != -1 and is_mock != "all":
                conditions.append("is_mock = ?")
                params.append(int(is_mock))
            if class_names is not None:
                if not class_names:
                    return []
                placeholders = ",".join("?" for _ in class_names)
                conditions.append(f"class_name IN ({placeholders})")
                params.extend(class_names)
            where_clause = " AND ".join(conditions)
            cur.execute(f"SELECT id, student_code, full_name, class_name, photo_path, is_mock FROM students WHERE {where_clause} ORDER BY id ASC", tuple(params))
            return [dict(r) for r in cur.fetchall()]

    def load_cached_embeddings(self, is_mock: int | str | None = None):
        """Nạp và giải mã toàn bộ vector sinh trắc học vào RAM để so khớp tức thì."""
        if is_mock is None:
            is_mock = 1 if self.is_debug else 0
        cache = {}
        with self._get_connection() as conn:
            cur = conn.cursor()
            if is_mock == -1 or is_mock == "all":
                cur.execute("SELECT id, student_code, full_name, encrypted_embedding FROM students WHERE is_active = 1")
                rows = cur.fetchall()
            else:
                cur.execute("SELECT id, student_code, full_name, encrypted_embedding FROM students WHERE is_active = 1 AND is_mock = ?", (int(is_mock),))
                rows = cur.fetchall()
            for row in rows:
                try:
                    vec = self.decrypt_embedding(row["encrypted_embedding"])
                    cache[row["id"]] = {
                        "id": row["id"],
                        "student_code": row["student_code"],
                        "full_name": row["full_name"],
                        "embedding": vec
                    }
                except Exception as e:
                    print(f"Lỗi giải mã sinh trắc học cho SV ID {row['id']}: {e}")
        return cache

    def get_all_sessions(self, only_active: bool = True) -> list[dict]:
        with self._get_connection() as conn:
            cur = conn.cursor()
            sql = "SELECT * FROM sessions"
            if only_active:
                sql += " WHERE is_active = 1"
            sql += " ORDER BY sort_order ASC, id ASC"
            cur.execute(sql)
            return [dict(r) for r in cur.fetchall()]

    def get_session(self, session_id: int) -> dict | None:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def add_session(self, name: str, checkin_open: str, class_start: str, late_threshold: str,
                    checkin_cutoff: str, early_threshold: str, class_end: str,
                    transition_cutoff: str = "12:00", is_active: int = 1, sort_order: int = 0) -> int:
        with self._get_connection() as conn:
            cur = conn.cursor()
            target_id = self._get_lowest_available_id("sessions", conn)
            cur.execute("""
            INSERT INTO sessions (id, name, checkin_open, class_start, late_threshold, checkin_cutoff, early_threshold, class_end, transition_cutoff, is_active, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (target_id, name, checkin_open, class_start, late_threshold, checkin_cutoff, early_threshold, class_end, transition_cutoff, is_active, sort_order))
            self._sync_sqlite_sequence("sessions", conn)
            conn.commit()
            return target_id

    def update_session(self, session_id: int, name: str, checkin_open: str, class_start: str, late_threshold: str,
                       checkin_cutoff: str, early_threshold: str, class_end: str,
                       transition_cutoff: str = "12:00", is_active: int = 1, sort_order: int = 0):
        with self._get_connection() as conn:
            conn.execute("""
            UPDATE sessions
            SET name = ?, checkin_open = ?, class_start = ?, late_threshold = ?, checkin_cutoff = ?, early_threshold = ?, class_end = ?, transition_cutoff = ?, is_active = ?, sort_order = ?
            WHERE id = ?
            """, (name, checkin_open, class_start, late_threshold, checkin_cutoff, early_threshold, class_end, transition_cutoff, is_active, sort_order, session_id))
            conn.commit()

    def delete_session(self, session_id: int) -> bool:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM sessions WHERE is_active = 1")
            if cur.fetchone()[0] <= 1:
                return False
            # Kiểm tra xem ca học đã có dữ liệu điểm danh hay chưa (khóa ngoại)
            cur.execute("SELECT COUNT(*) FROM attendance WHERE session_id = ?", (session_id,))
            has_att = cur.fetchone()[0] > 0
            cur.execute("SELECT COUNT(*) FROM attendance_debug WHERE session_id = ?", (session_id,))
            has_att_dbg = cur.fetchone()[0] > 0
            if has_att or has_att_dbg:
                cur.execute("UPDATE sessions SET is_active = 0 WHERE id = ?", (session_id,))
            else:
                cur.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
                self._sync_sqlite_sequence("sessions", conn)
            conn.commit()
            return cur.rowcount > 0

    def get_today_attendance(self, student_id: int, today_str: str = None, session_id: int = None):
        if today_str is None:
            today_str = date.today().isoformat()
        tbl = self._get_attendance_table()
        with self._get_connection() as conn:
            cur = conn.cursor()
            if session_id is not None:
                cur.execute(f"SELECT * FROM {tbl} WHERE student_id = ? AND date = ? AND session_id = ?", (student_id, today_str, session_id))
            else:
                cur.execute(f"SELECT * FROM {tbl} WHERE student_id = ? AND date = ? ORDER BY id DESC LIMIT 1", (student_id, today_str))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_attendance_record(self, student_id: int, today_str: str = None, session_id: int = None):
        """Bí danh của get_today_attendance để tương thích."""
        return self.get_today_attendance(student_id, today_str=today_str, session_id=session_id)

    def record_check_in(self, student_id: int, time_str: str, status: str, today_str: str = None, session_id: int = 1):
        if today_str is None:
            today_str = date.today().isoformat()
        if session_id is None:
            session_id = 1
        tbl = self._get_attendance_table()
        with self._get_connection() as conn:
            if self.is_debug:
                conn.execute(f"""
                INSERT INTO {tbl} (student_id, session_id, date, check_in_time, status)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(student_id, date, session_id) DO UPDATE SET
                    check_in_time = excluded.check_in_time,
                    check_out_time = NULL,
                    status = excluded.status
                """, (student_id, session_id, today_str, time_str, status))
            else:
                conn.execute(f"""
                INSERT INTO {tbl} (student_id, session_id, date, check_in_time, status)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(student_id, date, session_id) DO UPDATE SET
                    check_in_time = coalesce({tbl}.check_in_time, excluded.check_in_time),
                    status = excluded.status
                """, (student_id, session_id, today_str, time_str, status))
            conn.commit()

    def record_check_out(self, student_id: int, time_str: str, status: str, today_str: str = None, session_id: int = 1):
        if today_str is None:
            today_str = date.today().isoformat()
        if session_id is None:
            session_id = 1
        tbl = self._get_attendance_table()
        with self._get_connection() as conn:
            conn.execute(f"""
            UPDATE {tbl} 
            SET check_out_time = ?, status = ?
            WHERE student_id = ? AND date = ? AND session_id = ?
            """, (time_str, status, student_id, today_str, session_id))
            conn.commit()

    def get_attendance_logs(self, selected_date: str = None, session_id: int = None, class_names: list[str] | None = None):
        if selected_date is None:
            selected_date = date.today().isoformat()
        if class_names is not None and len(class_names) == 0:
            return []
        tbl = self._get_attendance_table()
        with self._get_connection() as conn:
            cur = conn.cursor()
            query = f"""
            SELECT a.id, s.student_code, s.full_name, s.class_name, a.date, 
                   a.check_in_time, a.check_out_time, a.status, a.note,
                   a.session_id, COALESCE(sess.name, 'Ca ' || a.session_id) AS session_name
            FROM {tbl} a
            JOIN students s ON a.student_id = s.id
            LEFT JOIN sessions sess ON a.session_id = sess.id
            WHERE a.date = ?
            """
            params = [selected_date]
            if session_id is not None:
                query += " AND a.session_id = ?"
                params.append(session_id)
            if class_names is not None:
                placeholders = ",".join("?" for _ in class_names)
                query += f" AND s.class_name IN ({placeholders})"
                params.extend(class_names)
            query += " ORDER BY a.id DESC"
            cur.execute(query, tuple(params))
            return [dict(r) for r in cur.fetchall()]

    def mark_absent_for_unrecorded(self, today_str: str = None, session_id: int = 1, class_names: list[str] | None = None, is_mock: int | str | None = None):
        """Đánh dấu Vắng cho các sinh viên hoạt động nhưng chưa có bản ghi trong ngày theo ca."""
        if is_mock is None:
            is_mock = 1 if self.is_debug else 0
        if today_str is None:
            today_str = date.today().isoformat()
        if session_id is None:
            session_id = 1
        if class_names is not None and len(class_names) == 0:
            return
        tbl = self._get_attendance_table()
        with self._get_connection() as conn:
            mock_cond = "" if (is_mock == -1 or is_mock == "all") else "AND s.is_mock = ?"
            insert_sql = f"""
            INSERT INTO {tbl} (student_id, session_id, date, status, note)
            SELECT s.id, ?, ?, 'VANG', 'Tự động chốt vắng sau giờ đóng cổng'
            FROM students s
            WHERE s.is_active = 1
              {mock_cond}
              AND s.id NOT IN (SELECT student_id FROM {tbl} WHERE date = ? AND session_id = ?)
            """
            params = [session_id, today_str]
            if mock_cond:
                params.append(int(is_mock))
            params.extend([today_str, session_id])
            if class_names is not None:
                placeholders = ",".join("?" for _ in class_names)
                insert_sql += f" AND s.class_name IN ({placeholders})"
                params.extend(class_names)
            insert_sql += " ON CONFLICT(student_id, date, session_id) DO NOTHING"
            conn.execute(insert_sql, tuple(params))
            conn.commit()

    def get_setting(self, key: str, default: str = "") -> str:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cur.fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        with self._get_connection() as conn:
            conn.execute("""
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """, (key, str(value)))
            conn.commit()

    def log_alert(self, camera_source: str, description: str, snapshot_path: str = ""):
        with self._get_connection() as conn:
            conn.execute("""
            INSERT INTO alerts (camera_source, snapshot_path, description)
            VALUES (?, ?, ?)
            """, (camera_source, snapshot_path, description))
            conn.commit()

    def delete_attendance_by_date(self, date_str: str, class_names: list[str] | None = None) -> int:
        """Xóa toàn bộ bản ghi điểm danh trong một ngày (tùy theo bảng đang hoạt động và lớp lọc)."""
        if class_names is not None and len(class_names) == 0:
            return 0
        tbl = self._get_attendance_table()
        with self._get_connection() as conn:
            cur = conn.cursor()
            if class_names is not None:
                placeholders = ",".join("?" for _ in class_names)
                cur.execute(f"""
                DELETE FROM {tbl} 
                WHERE date = ? 
                  AND student_id IN (SELECT id FROM students WHERE class_name IN ({placeholders}))
                """, (date_str, *class_names))
            else:
                cur.execute(f"DELETE FROM {tbl} WHERE date = ?", (date_str,))
            deleted_count = cur.rowcount
            cur.execute(f"SELECT COUNT(*) AS count FROM {tbl}")
            cnt_row = cur.fetchone()
            if cnt_row and cnt_row["count"] == 0:
                conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (tbl,))
            conn.commit()
            return deleted_count

    def delete_attendance_by_id(self, attendance_id: int) -> bool:
        """Xóa 1 bản ghi điểm danh cụ thể theo ID."""
        tbl = self._get_attendance_table()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(f"DELETE FROM {tbl} WHERE id = ?", (attendance_id,))
            deleted = cur.rowcount > 0
            cur.execute(f"SELECT COUNT(*) AS count FROM {tbl}")
            cnt_row = cur.fetchone()
            if cnt_row and cnt_row["count"] == 0:
                conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (tbl,))
            conn.commit()
            return deleted

    def clear_all_debug_attendance(self) -> int:
        """Xóa sạch toàn bộ dữ liệu điểm danh trong bảng Debug."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM attendance_debug")
            deleted_count = cur.rowcount
            conn.execute("DELETE FROM sqlite_sequence WHERE name = 'attendance_debug'")
            conn.commit()
            return deleted_count

    # ------------------ Quản lý Tài khoản Quản trị & Xác thực PBKDF2 ------------------

    def hash_password(self, password: str) -> str:
        """Băm mật khẩu bằng PBKDF2-HMAC-SHA256 kèm muối ngẫu nhiên 16 byte (600.000 vòng)."""
        salt = secrets.token_hex(16)
        iterations = 600000
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
        return f"pbkdf2:sha256:{iterations}${salt}${dk.hex()}"

    def verify_password(self, password: str, stored_hash: str) -> bool:
        """Xác thực mật khẩu với chuỗi hash đã lưu bằng so khớp thời gian không đổi."""
        if not stored_hash:
            return False
        if not stored_hash.startswith("pbkdf2:sha256:"):
            # Fallback tương thích ngược nếu hash cũ lưu chuỗi thô
            return secrets.compare_digest(password, stored_hash)
        try:
            prefix, salt, expected_dk_hex = stored_hash.split("$")
            iterations = int(prefix.split(":")[-1])
            computed_dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
            return secrets.compare_digest(computed_dk.hex(), expected_dk_hex)
        except Exception:
            return False

    def authenticate_admin(self, username: str, password: str) -> tuple[bool, str, dict | None]:
        """
        Xác thực đăng nhập tài khoản quản trị.
        Trả về (thành_công, thông_báo_lỗi, thông_tin_tài_khoản).
        """
        username = (username or "").strip()
        password = password or ""
        if not username or not password:
            return False, "Vui lòng nhập đầy đủ tên đăng nhập và mật khẩu.", None

        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM admin_users WHERE username = ?", (username,))
            user = cur.fetchone()
            if not user:
                return False, "Tài khoản hoặc mật khẩu không chính xác.", None

            user_dict = dict(user)
            if not user_dict.get("is_active", 1):
                return False, "Tài khoản này hiện đang bị khóa. Vui lòng liên hệ quản trị viên cấp cao.", None

            # Kiểm tra thời gian khóa tạm thời do nhập sai quá 5 lần
            locked_until_str = user_dict.get("locked_until")
            if locked_until_str:
                try:
                    locked_until = datetime.fromisoformat(locked_until_str)
                    if datetime.now() < locked_until:
                        diff_sec = max(1, int((locked_until - datetime.now()).total_seconds()))
                        diff = (diff_sec + 59) // 60
                        rem_m = diff_sec // 60
                        rem_s = diff_sec % 60
                        time_detail = f"{rem_m} phút {rem_s:02d} giây" if rem_m > 0 else f"{rem_s} giây"
                        return False, f"Tài khoản bị tạm khóa do nhập sai nhiều lần. Vui lòng thử lại sau {diff} phút (còn {time_detail}).", {
                            "locked_until": locked_until_str,
                            "remaining_seconds": diff_sec
                        }
                    else:
                        # Đã hết thời hạn khóa 15 phút -> giải phóng khóa và đặt lại số lần thử sai về 0
                        cur.execute("UPDATE admin_users SET failed_attempts = 0, locked_until = NULL, last_failed_at = NULL WHERE id = ?", (user_dict["id"],))
                        conn.commit()
                        user_dict["failed_attempts"] = 0
                        user_dict["locked_until"] = None
                        user_dict["last_failed_at"] = None
                except Exception:
                    pass

            # Kiểm tra thời gian hồi phục số lần nhập sai (rolling cooldown window) khi chưa đủ 5 lần
            cooldown_mins = 15
            try:
                cooldown_val = self.get_setting("failed_attempt_cooldown_mins", "15")
                cooldown_mins = max(1, int(cooldown_val))
            except Exception:
                cooldown_mins = 15

            last_failed_str = user_dict.get("last_failed_at")
            if user_dict.get("failed_attempts", 0) > 0 and last_failed_str and not user_dict.get("locked_until"):
                try:
                    last_failed_dt = datetime.fromisoformat(last_failed_str)
                    if datetime.now() >= last_failed_dt + timedelta(minutes=cooldown_mins):
                        # Đã hết thời gian chờ -> tự động đặt lại số lần nhập sai về 0
                        cur.execute("UPDATE admin_users SET failed_attempts = 0, last_failed_at = NULL WHERE id = ?", (user_dict["id"],))
                        conn.commit()
                        user_dict["failed_attempts"] = 0
                        user_dict["last_failed_at"] = None
                except Exception:
                    pass

            if self.verify_password(password, user_dict["password_hash"]):
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cur.execute("""
                UPDATE admin_users
                SET failed_attempts = 0, locked_until = NULL, last_failed_at = NULL, last_login = ?
                WHERE id = ?
                """, (now_str, user_dict["id"]))
                conn.commit()
                self.log_admin_audit(user_dict["id"], user_dict["username"], "LOGIN_SUCCESS", "Đăng nhập thành công")
                user_dict["last_login"] = now_str
                user_dict["failed_attempts"] = 0
                user_dict["locked_until"] = None
                user_dict["last_failed_at"] = None
                return True, "", user_dict
            else:
                attempts = user_dict.get("failed_attempts", 0) + 1
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if attempts >= 5:
                    lock_until_dt = datetime.now() + timedelta(minutes=15)
                    lock_time = lock_until_dt.strftime("%Y-%m-%d %H:%M:%S")
                    cur.execute("""
                    UPDATE admin_users
                    SET failed_attempts = ?, locked_until = ?, last_failed_at = ?
                    WHERE id = ?
                    """, (attempts, lock_time, now_str, user_dict["id"]))
                    conn.commit()
                    self.log_admin_audit(user_dict["id"], user_dict["username"], "ACCOUNT_LOCKED", "Khóa tạm 15 phút do nhập sai 5 lần")
                    return False, "Bạn đã nhập sai mật khẩu 5 lần liên tiếp. Tài khoản đã bị tạm khóa trong 15 phút (còn 15 phút 00 giây).", {
                        "locked_until": lock_time,
                        "remaining_seconds": 900
                    }
                else:
                    cur.execute("""
                    UPDATE admin_users
                    SET failed_attempts = ?, last_failed_at = ?
                    WHERE id = ?
                    """, (attempts, now_str, user_dict["id"]))
                    conn.commit()
                    self.log_admin_audit(user_dict["id"], user_dict["username"], "LOGIN_FAILED", f"Nhập sai mật khẩu lần {attempts}")
                    remaining = 5 - attempts
                    return False, f"Mật khẩu không chính xác. Bạn còn {remaining} lần thử trước khi tài khoản bị khóa tạm.", None

    def get_admin_user_by_id(self, user_id: int) -> dict | None:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM admin_users WHERE id = ?", (user_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_admin_user_by_username(self, username: str) -> dict | None:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM admin_users WHERE username = ?", (username.strip(),))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_all_admin_users(self) -> list[dict]:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, username, full_name, role, avatar_path, is_active, failed_attempts, locked_until, last_failed_at, last_login, created_at FROM admin_users ORDER BY id ASC")
            return [dict(r) for r in cur.fetchall()]

    def update_admin_profile(self, user_id: int, full_name: str, avatar_path: str, actor_username: str = "") -> tuple[bool, str]:
        full_name = (full_name or "").strip()
        if not full_name:
            return False, "Họ và tên không được để trống."
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT avatar_path FROM admin_users WHERE id = ?", (user_id,))
            row = cur.fetchone()
            old_avatar = (row["avatar_path"] if row else "") or ""
            if old_avatar and old_avatar != avatar_path:
                if os.path.exists(old_avatar):
                    try:
                        os.remove(old_avatar)
                    except Exception:
                        pass
                base_old, _ = os.path.splitext(old_avatar)
                for ext in [".enc", ".jpg", ".jpeg", ".png", ".bmp", ".webp"]:
                    alt_p = f"{base_old}{ext}"
                    if alt_p != avatar_path and os.path.exists(alt_p):
                        try:
                            os.remove(alt_p)
                        except Exception:
                            pass
            cur.execute("""
            UPDATE admin_users
            SET full_name = ?, avatar_path = ?
            WHERE id = ?
            """, (full_name, avatar_path, user_id))
            conn.commit()
            self.log_admin_audit(user_id, actor_username or "SYSTEM", "PROFILE_UPDATE", f"Cập nhật hồ sơ tài khoản id {user_id}: {full_name}")
            return True, "Cập nhật thông tin hồ sơ thành công."

    def update_admin_username(self, user_id: int, new_username: str, actor_username: str = "") -> tuple[bool, str]:
        """Cập nhật tên đăng nhập cho tài khoản quản trị (ngoại trừ tài khoản admin gốc)."""
        new_username = (new_username or "").strip()
        if not new_username:
            return False, "Tên đăng nhập không được để trống."
        if len(new_username) < 3:
            return False, "Tên đăng nhập phải có tối thiểu 3 ký tự."
        import re
        if not re.match(r"^[a-zA-Z0-9_.-]+$", new_username):
            return False, "Tên đăng nhập chỉ được chứa chữ cái, số, dấu gạch dưới, gạch ngang và dấu chấm."
        if new_username.lower() == "admin":
            return False, "Tên đăng nhập 'admin' được bảo lưu cho tài khoản quản trị viên gốc."

        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM admin_users WHERE id = ?", (user_id,))
            user = cur.fetchone()
            if not user:
                return False, "Không tìm thấy thông tin tài khoản người dùng."
            user_dict = dict(user)
            old_username = user_dict["username"]
            if old_username.lower() == "admin":
                return False, "Không được phép thay đổi tên đăng nhập của tài khoản admin gốc."

            if old_username.lower() == new_username.lower():
                return True, "Tên đăng nhập không thay đổi."

            cur.execute("SELECT id FROM admin_users WHERE username = ? AND id != ?", (new_username, user_id))
            if cur.fetchone():
                return False, f"Tên đăng nhập '{new_username}' đã được sử dụng bởi một tài khoản khác."

            new_avatar_path = user_dict.get("avatar_path") or ""
            if new_avatar_path and os.path.exists(new_avatar_path):
                _, ext = os.path.splitext(new_avatar_path)
                target_avatar = f"data/avatars/{new_username}_avatar{ext.lower()}"
                try:
                    os.makedirs("data/avatars", exist_ok=True)
                    if os.path.abspath(new_avatar_path) != os.path.abspath(target_avatar):
                        import shutil
                        shutil.move(new_avatar_path, target_avatar)
                        new_avatar_path = target_avatar
                except Exception:
                    pass

            cur.execute("UPDATE admin_users SET username = ?, avatar_path = ? WHERE id = ?", (new_username, new_avatar_path, user_id))
            conn.commit()
            self.log_admin_audit(user_id, actor_username or old_username, "USERNAME_CHANGE", f"Đổi tên đăng nhập từ '{old_username}' thành '{new_username}'")
            return True, "Cập nhật tên đăng nhập thành công."

    def change_admin_password(self, user_id: int, old_password: str, new_password: str, actor_username: str = "", force: bool = False) -> tuple[bool, str]:
        if not new_password or len(new_password) < 6:
            return False, "Mật khẩu mới phải có tối thiểu 6 ký tự."
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM admin_users WHERE id = ?", (user_id,))
            user = cur.fetchone()
            if not user:
                return False, "Không tìm thấy thông tin tài khoản người dùng."
            user_dict = dict(user)
            if not force:
                if not self.verify_password(old_password, user_dict["password_hash"]):
                    return False, "Mật khẩu hiện tại không chính xác."

            new_hash = self.hash_password(new_password)
            cur.execute("UPDATE admin_users SET password_hash = ? WHERE id = ?", (new_hash, user_id))
            conn.commit()
            self.log_admin_audit(user_id, actor_username or user_dict["username"], "PASSWORD_CHANGE", f"Đổi mật khẩu tài khoản {user_dict['username']}")
            return True, "Đổi mật khẩu thành công."

    def create_admin_user(self, username: str, full_name: str, password: str, role: str = "manager", avatar_path: str = "", actor_username: str = "") -> tuple[bool, str]:
        username = (username or "").strip()
        if not username or not full_name:
            return False, "Tên đăng nhập và Họ tên không được để trống."
        if not password or len(password) < 6:
            return False, "Mật khẩu phải có tối thiểu 6 ký tự."
        if role not in ("super_admin", "manager", "teacher", "technician"):
            raise ValueError(f"Vai trò '{role}' không hợp lệ. Chỉ chấp nhận: super_admin, manager, teacher, technician.")
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM admin_users WHERE username = ?", (username,))
            if cur.fetchone():
                return False, f"Tên đăng nhập '{username}' đã tồn tại trong hệ thống."
            pwd_hash = self.hash_password(password)
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            target_id = self._get_lowest_available_id("admin_users", conn)
            cur.execute("""
            INSERT INTO admin_users (id, username, full_name, password_hash, role, avatar_path, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            """, (target_id, username, full_name, pwd_hash, role, avatar_path, now_str))
            self._sync_sqlite_sequence("admin_users", conn)
            conn.commit()
            new_id = target_id
            self.log_admin_audit(new_id, actor_username or "admin", "USER_CREATE", f"Tạo tài khoản mới '{username}' vai trò '{role}'")
            return True, "Tạo tài khoản quản trị mới thành công."

    def toggle_admin_user_active(self, user_id: int, actor_username: str = "") -> tuple[bool, str]:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM admin_users WHERE id = ?", (user_id,))
            user = cur.fetchone()
            if not user:
                return False, "Không tìm thấy người dùng."
            user_dict = dict(user)

            is_temp_locked = False
            locked_until_str = user_dict.get("locked_until")
            if locked_until_str:
                try:
                    locked_until = datetime.fromisoformat(locked_until_str)
                    if datetime.now() < locked_until:
                        is_temp_locked = True
                except Exception:
                    pass

            if actor_username and actor_username.lower() == user_dict["username"].lower():
                return False, "Không thể tự khóa tài khoản của chính mình."

            if user_dict["username"].lower() == "admin":
                if is_temp_locked:
                    cur.execute("UPDATE admin_users SET is_active = 1, locked_until = NULL, failed_attempts = 0, last_failed_at = NULL WHERE id = ?", (user_id,))
                    conn.commit()
                    self.log_admin_audit(user_id, actor_username or "admin", "USER_LOCK", "Mở khóa tài khoản admin gốc do nhập sai mật khẩu")
                    return True, "Đã mở khóa tài khoản quản trị viên gốc (admin) thành công."
                return False, "Không thể khóa tài khoản quản trị viên gốc (admin)."

            if is_temp_locked:
                cur.execute("UPDATE admin_users SET is_active = 1, locked_until = NULL, failed_attempts = 0, last_failed_at = NULL WHERE id = ?", (user_id,))
                conn.commit()
                self.log_admin_audit(user_id, actor_username or "admin", "USER_LOCK", f"Mở khóa tài khoản bị tạm khóa '{user_dict['username']}'")
                return True, f"Đã mở khóa tài khoản '{user_dict['username']}' thành công."
            else:
                new_state = 0 if user_dict["is_active"] else 1
                cur.execute("UPDATE admin_users SET is_active = ?, locked_until = NULL, failed_attempts = 0, last_failed_at = NULL WHERE id = ?", (new_state, user_id))
                conn.commit()
                st_text = "Mở khóa" if new_state else "Khóa"
                self.log_admin_audit(user_id, actor_username or "admin", "USER_LOCK", f"{st_text} tài khoản '{user_dict['username']}'")
                return True, f"Đã {st_text.lower()} tài khoản thành công."

    def unlock_admin_user(self, user_id: int, actor_username: str = "") -> tuple[bool, str]:
        """Mở khóa tài khoản quản trị viên (giải phóng trạng thái tạm khóa hoặc bị vô hiệu hóa)."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM admin_users WHERE id = ?", (user_id,))
            user = cur.fetchone()
            if not user:
                return False, "Không tìm thấy người dùng."
            user_dict = dict(user)
            cur.execute("UPDATE admin_users SET is_active = 1, locked_until = NULL, failed_attempts = 0, last_failed_at = NULL WHERE id = ?", (user_id,))
            conn.commit()
            self.log_admin_audit(user_id, actor_username or "admin", "USER_LOCK", f"Mở khóa tài khoản '{user_dict['username']}'")
            return True, f"Đã mở khóa tài khoản '{user_dict['username']}' thành công."

    def delete_admin_user(self, user_id: int, actor_username: str = "") -> tuple[bool, str]:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM admin_users WHERE id = ?", (user_id,))
            user = cur.fetchone()
            if not user:
                return False, "Không tìm thấy người dùng."
            user_dict = dict(user)
            if actor_username and actor_username.lower() == user_dict["username"].lower():
                return False, "Không thể tự xóa tài khoản của chính mình."
            if user_dict["username"].lower() == "admin":
                return False, "Không thể xóa tài khoản quản trị viên gốc (admin)."
            avatar_path = user_dict.get("avatar_path")
            if avatar_path:
                if os.path.exists(avatar_path):
                    try:
                        os.remove(avatar_path)
                    except Exception:
                        pass
                base_av, _ = os.path.splitext(avatar_path)
                for ext in [".enc", ".jpg", ".jpeg", ".png", ".bmp", ".webp"]:
                    alt_p = f"{base_av}{ext}"
                    if os.path.exists(alt_p):
                        try:
                            os.remove(alt_p)
                        except Exception:
                            pass
            cur.execute("DELETE FROM admin_user_classes WHERE user_id = ?", (user_id,))
            cur.execute("DELETE FROM admin_users WHERE id = ?", (user_id,))
            self._sync_sqlite_sequence("admin_users", conn)
            self._sync_sqlite_sequence("admin_user_classes", conn)
            conn.commit()
            self.log_admin_audit(user_id, actor_username or "admin", "USER_DELETE", f"Xóa tài khoản '{user_dict['username']}'")
            return True, "Xóa tài khoản thành công."

    def reset_emergency_admin(self, username: str, new_password: str) -> bool:
        """Cứu hộ khẩn cấp: mở khóa và cập nhật mật khẩu mới qua Terminal."""
        username = (username or "admin").strip()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM admin_users WHERE username = ?", (username,))
            user = cur.fetchone()
            pwd_hash = self.hash_password(new_password)
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if user:
                cur.execute("""
                UPDATE admin_users
                SET password_hash = ?, is_active = 1, failed_attempts = 0, locked_until = NULL
                WHERE id = ?
                """, (pwd_hash, user["id"]))
                self.log_admin_audit(user["id"], "EMERGENCY", "RESET_PASSWORD", f"Cứu hộ mật khẩu cho tài khoản '{username}'")
            else:
                cur.execute("""
                INSERT INTO admin_users (username, full_name, password_hash, role, is_active, created_at)
                VALUES (?, 'Quản trị viên Hệ thống', ?, 'super_admin', 1, ?)
                """, (username, pwd_hash, now_str))
                self.log_admin_audit(None, "EMERGENCY", "CREATE_ADMIN", f"Khởi tạo khẩn cấp tài khoản '{username}'")
            conn.commit()
            return True

    def log_admin_audit(self, user_id: int | None, username: str, action: str, details: str = "", target_entity: str = ""):
        try:
            with self._get_connection() as conn:
                conn.execute("""
                INSERT INTO admin_audit_logs (user_id, username, action, details, target_entity)
                VALUES (?, ?, ?, ?, ?)
                """, (user_id, username, action, details, target_entity))
                conn.commit()
        except Exception:
            pass

    def get_admin_audit_logs(self, limit: int = 100) -> list[dict]:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, timestamp, username, action, details, target_entity FROM admin_audit_logs ORDER BY id DESC LIMIT ?", (limit,))
            return [dict(r) for r in cur.fetchall()]

    # --- Quản Lý Danh Mục Lớp Học & Phân Công Giảng Dạy ---

    def get_all_classes(self, is_mock: int | str | None = None) -> list[str]:
        """Lấy danh sách tất cả các tên lớp học đã tạo trong hệ thống (sắp xếp A-Z)."""
        if is_mock is None:
            is_mock = 1 if self.is_debug else 0
        with self._get_connection() as conn:
            cur = conn.cursor()
            if is_mock == -1 or is_mock == "all":
                cur.execute("SELECT name FROM classes ORDER BY name COLLATE NOCASE ASC")
            else:
                cur.execute("SELECT name FROM classes WHERE is_mock = ? ORDER BY name COLLATE NOCASE ASC", (int(is_mock),))
            return [r["name"] for r in cur.fetchall()]

    def get_classes_with_counts(self, is_mock: int | str | None = None) -> list[dict]:
        """Lấy danh sách lớp kèm số lượng sinh viên đang theo học từng lớp."""
        if is_mock is None:
            is_mock = 1 if self.is_debug else 0
        with self._get_connection() as conn:
            cur = conn.cursor()
            if is_mock == -1 or is_mock == "all":
                cur.execute("""
                SELECT c.id, c.name, c.is_mock, c.created_at,
                       (SELECT COUNT(*) FROM students s WHERE trim(s.class_name) = trim(c.name) AND s.is_active = 1) AS student_count
                FROM classes c
                ORDER BY c.name COLLATE NOCASE ASC
                """)
            else:
                cur.execute("""
                SELECT c.id, c.name, c.is_mock, c.created_at,
                       (SELECT COUNT(*) FROM students s WHERE trim(s.class_name) = trim(c.name) AND s.is_active = 1 AND s.is_mock = ?) AS student_count
                FROM classes c
                WHERE c.is_mock = ?
                ORDER BY c.name COLLATE NOCASE ASC
                """, (int(is_mock), int(is_mock)))
            return [dict(r) for r in cur.fetchall()]

    def add_class(self, name: str, is_mock: int | None = None) -> tuple[bool, str]:
        """Thêm lớp học mới vào danh mục hệ thống (cô lập sandbox theo is_mock)."""
        if is_mock is None:
            is_mock = 1 if self.is_debug else 0
        name = (name or "").strip()
        if not name:
            return False, "Tên lớp học không được để trống."
        try:
            with self._get_connection() as conn:
                target_id = self._get_lowest_available_id("classes", conn, is_mock=int(is_mock))
                conn.execute("INSERT INTO classes (id, name, is_mock) VALUES (?, ?, ?)", (target_id, name, int(is_mock)))
                self._sync_sqlite_sequence("classes", conn)
                conn.commit()
            return True, f"Đã thêm lớp '{name}' thành công."
        except sqlite3.IntegrityError:
            return False, f"Lớp '{name}' đã tồn tại trong hệ thống."
        except Exception as e:
            return False, f"Lỗi tạo lớp học: {e}"

    def delete_class(self, name: str, is_mock: int | None = None) -> tuple[bool, str]:
        """Xóa lớp học khỏi danh mục nếu chưa có sinh viên nào theo học (theo phân vùng is_mock)."""
        if is_mock is None:
            is_mock = 1 if self.is_debug else 0
        name = (name or "").strip()
        if not name:
            return False, "Tên lớp không hợp lệ."
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) AS count FROM students WHERE trim(class_name) = ? AND is_active = 1 AND is_mock = ?", (name, int(is_mock)))
            cnt = cur.fetchone()["count"]
            if cnt > 0:
                return False, f"Không thể xóa lớp '{name}' vì đang có {cnt} sinh viên thuộc lớp này."
            conn.execute("DELETE FROM classes WHERE trim(name) = ? AND is_mock = ?", (name, int(is_mock)))
            if int(is_mock) == 0:
                conn.execute("DELETE FROM admin_user_classes WHERE trim(class_name) = ?", (name,))
                self._sync_sqlite_sequence("admin_user_classes", conn)
            self._sync_sqlite_sequence("classes", conn)
            conn.commit()
            return True, f"Đã xóa lớp '{name}' thành công."

    def get_admin_user_classes(self, user_id: int) -> list[str]:
        """Lấy danh sách các lớp học được phân công cho một tài khoản quản trị."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT class_name FROM admin_user_classes WHERE user_id = ? ORDER BY class_name COLLATE NOCASE ASC", (user_id,))
            return [r["class_name"] for r in cur.fetchall()]

    def set_admin_user_classes(self, user_id: int, class_names: list[str], actor_username: str = "") -> bool:
        """Cập nhật danh sách lớp học được phân công cho một tài khoản quản trị (trong transaction)."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM admin_user_classes WHERE user_id = ?", (user_id,))
            for c in class_names:
                c = c.strip()
                if c:
                    cur.execute("INSERT OR IGNORE INTO admin_user_classes (user_id, class_name) VALUES (?, ?)", (user_id, c))
            conn.commit()
        u = self.get_admin_user_by_id(user_id)
        uname = u["username"] if u else f"ID {user_id}"
        cls_str = ", ".join(class_names) if class_names else "Không có lớp"
        self.log_admin_audit(user_id, actor_username or "admin", "USER_ASSIGN_CLASSES", f"Phân công lớp cho '{uname}': {cls_str}")
        return True

    def get_all_teachers(self) -> list[dict]:
        """Lấy danh sách tất cả các tài khoản Giáo viên (role = 'teacher') kèm lớp đã phân công."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
            SELECT id, username, full_name, role, avatar_path, is_active, last_login
            FROM admin_users
            WHERE role = 'teacher'
            ORDER BY id ASC
            """)
            teachers = [dict(r) for r in cur.fetchall()]
        for t in teachers:
            cls_list = self.get_admin_user_classes(t["id"])
            t["assigned_classes"] = cls_list
            t["classes"] = cls_list
        return teachers

    def get_mock_students(self) -> list[dict]:
        """Lấy danh sách các sinh viên thử nghiệm (is_mock = 1) phục vụ giả lập trong Debug Mode."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, student_code, full_name, class_name, photo_path, is_mock FROM students WHERE is_mock = 1 AND is_active = 1 ORDER BY student_code ASC")
            return [dict(r) for r in cur.fetchall()]

    def create_mock_students_and_classes(
        self,
        class_names: list[str] | None = None,
        num_students_per_class: int = 3,
        custom_names: list[str] | None = None
    ) -> tuple[int, int]:
        """Tạo N lớp học ảo và N sinh viên ảo phục vụ kiểm thử phân quyền và ca học."""
        if not class_names:
            class_names = ["LỚP_TEST_A", "LỚP_TEST_B"]

        for c in class_names:
            self.add_class(c, is_mock=1)

        ho_list = ["Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Vũ", "Đặng", "Bùi", "Đỗ", "Hồ"]
        dem_list = ["Văn", "Thị", "Minh", "Thanh", "Đình", "Đức", "Hữu", "Hoàng"]
        ten_list = ["An", "Bình", "Cường", "Dũng", "Giang", "Hải", "Khoa", "Linh", "Nam", "Phúc", "Quân", "Sơn", "Tuấn", "Vy", "Yến"]

        created_count = 0
        name_idx = 0
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT student_code FROM students WHERE student_code LIKE 'TEST%'")
            existing_codes = set(r["student_code"] for r in cur.fetchall())

        code_seq = 1
        for cname in class_names:
            for s_idx in range(num_students_per_class):
                while f"TEST{code_seq:03d}" in existing_codes:
                    code_seq += 1
                scode = f"TEST{code_seq:03d}"
                existing_codes.add(scode)

                if custom_names and name_idx < len(custom_names):
                    sname = custom_names[name_idx].strip()
                    name_idx += 1
                else:
                    h = ho_list[(code_seq * 3) % len(ho_list)]
                    d = dem_list[(code_seq * 5) % len(dem_list)]
                    t = ten_list[(code_seq * 7) % len(ten_list)]
                    sname = f"{h} {d} {t}"

                vec = np.random.randn(512).astype(np.float32)
                norm = np.linalg.norm(vec)
                if norm > 0:
                    vec /= norm

                self.add_student(scode, sname, cname, vec, photo_path="", is_mock=1)
                created_count += 1
                code_seq += 1

        if self.reload_cache_cb and callable(self.reload_cache_cb):
            try:
                self.reload_cache_cb()
            except Exception:
                pass
        return created_count, len(class_names)

    def clear_mock_students_and_classes(self) -> int:
        """Xóa sạch toàn bộ sinh viên ảo, điểm danh ảo và lớp học ảo (tự động dọn dẹp khi tắt Debug)."""
        deleted_count = 0
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, photo_path FROM students WHERE is_mock = 1")
            mock_rows = cur.fetchall()
            deleted_count = len(mock_rows)
            for r in mock_rows:
                p = r["photo_path"]
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass
            mock_ids = [r["id"] for r in mock_rows]
            if mock_ids:
                placeholders = ",".join("?" for _ in mock_ids)
                conn.execute(f"DELETE FROM attendance_debug WHERE student_id IN ({placeholders})", tuple(mock_ids))
                conn.execute(f"DELETE FROM attendance WHERE student_id IN ({placeholders})", tuple(mock_ids))
                conn.execute(f"DELETE FROM students WHERE id IN ({placeholders})", tuple(mock_ids))

            # Dọn dẹp các tệp ảnh mock còn sót trong data/photos
            photos_dir = "data/photos"
            if os.path.exists(photos_dir):
                for fname in os.listdir(photos_dir):
                    if fname.startswith("mock_"):
                        try:
                            os.remove(os.path.join(photos_dir, fname))
                        except Exception:
                            pass

            conn.execute("""
            DELETE FROM admin_user_classes 
            WHERE class_name IN (SELECT name FROM classes WHERE is_mock = 1) 
               OR class_name LIKE 'LỚP_TEST_%'
            """)
            conn.execute("DELETE FROM classes WHERE is_mock = 1")
            self._sync_sqlite_sequence("students", conn)
            self._sync_sqlite_sequence("attendance", conn)
            self._sync_sqlite_sequence("attendance_debug", conn)
            self._sync_sqlite_sequence("classes", conn)
            self._sync_sqlite_sequence("admin_user_classes", conn)
            conn.commit()

        if self.reload_cache_cb and callable(self.reload_cache_cb):
            try:
                self.reload_cache_cb()
            except Exception:
                pass
        return deleted_count
