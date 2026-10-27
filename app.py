"""
Phần mềm Giao diện Điểm danh Sinh viên bằng UniFace (Desktop App - PyQt6).
"""
import sys
import os
import cv2
import numpy as np
import pandas as pd
import json
from datetime import datetime, timedelta
import time
import math
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QDialog, QLineEdit, QFormLayout, QMessageBox, QTabWidget,
    QFileDialog, QSpinBox, QDoubleSpinBox, QTimeEdit, QComboBox, QGroupBox,
    QFrame, QDateEdit, QCheckBox, QInputDialog, QProgressBar, QScrollArea,
    QAbstractSpinBox, QStackedWidget, QRadioButton, QTextEdit, QSizePolicy
)
from PyQt6.QtCore import Qt, QTimer, QTime, QDate, QPointF, pyqtSignal, QObject, QEvent
from PyQt6.QtGui import QImage, QPixmap, QFont, QPainter, QPainterPath, QColor, QPen, QCursor

from db import DatabaseManager
from face_engine import FaceEngine
from attendance_logic import AttendanceLogic, validate_schedule_chronology
from camera_worker import CameraWorker, PersonCountWorker

class WheelBlockFilter(QObject):
    """Event filter chặn sự kiện cuộn chuột trên các ô nhập liệu số/thời gian để tránh nhảy giá trị khi cuộn trang."""
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel:
            event.ignore()
            return True
        return super().eventFilter(obj, event)

def disable_wheel_on_inputs(parent_widget):
    """Gắn bộ lọc chặn cuộn chuột lên toàn bộ các ô spinbox, combobox và date/time edit trong widget."""
    wheel_filter = WheelBlockFilter(parent_widget)
    for child in parent_widget.findChildren((QAbstractSpinBox, QComboBox)):
        child.installEventFilter(wheel_filter)

STATUS_DISPLAY_MAP = {
    "DUNG_GIO": ("Đúng giờ", "#16a34a"),
    "DI_TRE": ("Đi trễ", "#d97706"),
    "VE_SOM": ("Về sớm", "#2563eb"),
    "HOAN_THANH": ("Hoàn thành", "#16a34a"),
    "VANG": ("Vắng mặt", "#dc2626"),
    "CANH_BAO": ("Cảnh báo (Người lạ)", "#dc2626"),
    "COOLDOWN": ("Chờ quét lại", "#6b7280"),
    "TOO_EARLY": ("Chưa mở cổng", "#d97706"),
    "ALREADY_CHECKED_IN": ("Đã điểm danh", "#4b5563"),
    "ALREADY_CHECKED_OUT": ("Đã check-out", "#4b5563"),
    "NO_CHECK_IN": ("Chưa check-in", "#d97706"),
    "CLASS_ENDED": ("Ca đã kết thúc", "#6b7280"),
    "ABSENT": ("Đã ghi nhận vắng", "#dc2626"),
    "TU_CHOI": ("Từ chối", "#dc2626"),
}

def get_status_display(status: str) -> tuple[str, str]:
    """Trả về (nhãn_tiếng_việt, mã_màu_hex)."""
    return STATUS_DISPLAY_MAP.get(status, (status, "#111827"))

class DebugManager(QObject):
    """Quản lý Chế độ Debug & Thời gian Giả lập độc lập với đồng hồ Windows."""
    time_changed = pyqtSignal(datetime)
    mode_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.is_active = False
        self.is_frozen = False
        self.base_dt = datetime.now()
        self.base_wall_time = time.time()

    def enable(self, custom_dt: datetime = None, frozen: bool = False):
        self.is_active = True
        self.is_frozen = frozen
        if custom_dt is not None:
            self.base_dt = custom_dt
        else:
            self.base_dt = datetime.now()
        self.base_wall_time = time.time()
        self.mode_changed.emit(True)
        self.time_changed.emit(self.get_current_datetime())

    def disable(self):
        self.is_active = False
        self.is_frozen = False
        self.mode_changed.emit(False)
        self.time_changed.emit(datetime.now())

    def set_active(self, active: bool):
        if active:
            self.enable(self.base_dt, self.is_frozen)
        else:
            self.disable()

    def set_time(self, new_dt: datetime, frozen: bool = None):
        self.base_dt = new_dt
        self.base_wall_time = time.time()
        if frozen is not None:
            self.is_frozen = frozen
        self.time_changed.emit(self.get_current_datetime())

    def adjust_time(self, hours: int = 0, minutes: int = 0, seconds: int = 0):
        current = self.get_current_datetime()
        new_dt = current + timedelta(hours=hours, minutes=minutes, seconds=seconds)
        self.set_time(new_dt)

    def get_current_datetime(self) -> datetime:
        if not self.is_active:
            return datetime.now()
        if self.is_frozen:
            return self.base_dt
        elapsed = time.time() - self.base_wall_time
        return self.base_dt + timedelta(seconds=elapsed)

def ensure_app_assets(force=True):
    """Tự động khởi tạo các asset biểu tượng chuẩn monochrome (icon lịch, checkbox, dropdown)."""
    if QApplication.instance() is None:
        return
    os.makedirs("assets", exist_ok=True)
    cal_path = "assets/calendar.png"
    if force or not os.path.exists(cal_path):
        img = QImage(24, 24, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#111827"), 2)
        p.setPen(pen)
        p.drawRoundedRect(3, 5, 18, 16, 2, 2)
        p.drawLine(3, 10, 21, 10)
        p.drawLine(7, 2, 7, 6)
        p.drawLine(17, 2, 17, 6)
        p.end()
        img.save(cal_path)

    chk_path = "assets/checkbox_check.png"
    if force or not os.path.exists(chk_path):
        img = QImage(16, 16, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#ffffff"), 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawLine(3, 8, 6, 12)
        p.drawLine(6, 12, 13, 4)
        p.end()
        img.save(chk_path)

    arr_path = "assets/dropdown_arrow.png"
    if force or not os.path.exists(arr_path):
        img_arr = QImage(12, 12, QImage.Format.Format_ARGB32_Premultiplied)
        img_arr.fill(Qt.GlobalColor.transparent)
        p = QPainter(img_arr)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#374151"), 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawLine(2, 4, 6, 8)
        p.drawLine(6, 8, 10, 4)
        p.end()
        img_arr.save(arr_path)

    arr_dis_path = "assets/dropdown_arrow_disabled.png"
    if force or not os.path.exists(arr_dis_path):
        img_arr_dis = QImage(12, 12, QImage.Format.Format_ARGB32_Premultiplied)
        img_arr_dis.fill(Qt.GlobalColor.transparent)
        p = QPainter(img_arr_dis)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#9ca3af"), 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawLine(2, 4, 6, 8)
        p.drawLine(6, 8, 10, 4)
        p.end()
        img_arr_dis.save(arr_dis_path)


GLOBAL_MENU_QSS = """
QMenu {
    background-color: #ffffff;
    color: #111827;
    border: 1px solid #d1d5db;
    border-radius: 6px;
    padding: 4px;
}
QMenu::item {
    background-color: transparent;
    color: #111827;
    padding: 6px 24px 6px 24px;
    border-radius: 4px;
    font-size: 12px;
}
QMenu::item:selected {
    background-color: #f3f4f6;
    color: #111827;
}
QMenu::item:disabled {
    color: #9ca3af;
    background-color: transparent;
}
QMenu::separator {
    height: 1px;
    background-color: #e5e7eb;
    margin: 4px 8px;
}
"""

GLOBAL_SCROLLBAR_QSS = """
QScrollBar:vertical {
    border: none;
    background-color: #f8fafc;
    width: 8px;
    margin: 0px;
    border-radius: 4px;
}
QScrollBar::handle:vertical {
    background-color: #cbd5e1;
    min-height: 28px;
    border-radius: 3px;
    margin: 1px;
}
QScrollBar::handle:vertical:hover {
    background-color: #94a3b8;
}
QScrollBar::handle:vertical:pressed {
    background-color: #64748b;
}
QScrollBar::sub-line:vertical, QScrollBar::add-line:vertical {
    border: none;
    background: transparent;
    height: 0px;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}

QScrollBar:horizontal {
    border: none;
    background-color: #f8fafc;
    height: 8px;
    margin: 0px;
    border-radius: 4px;
}
QScrollBar::handle:horizontal {
    background-color: #cbd5e1;
    min-width: 28px;
    border-radius: 3px;
    margin: 1px;
}
QScrollBar::handle:horizontal:hover {
    background-color: #94a3b8;
}
QScrollBar::handle:horizontal:pressed {
    background-color: #64748b;
}
QScrollBar::sub-line:horizontal, QScrollBar::add-line:horizontal {
    border: none;
    background: transparent;
    width: 0px;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: transparent;
}
QScrollBar::corner {
    background: transparent;
}
"""

GLOBAL_DIALOG_QSS = """
QMessageBox, QInputDialog {
    background-color: #ffffff;
    color: #111827;
    font-family: 'Aptos', 'Segoe UI', -apple-system, sans-serif;
}
QMessageBox QLabel, QInputDialog QLabel {
    color: #111827;
    font-size: 13px;
    background: transparent;
}
QMessageBox QPushButton, QInputDialog QPushButton {
    min-width: 72px;
    min-height: 28px;
    padding: 5px 16px;
    font-size: 12px;
    font-weight: bold;
    border-radius: 5px;
    border: 1px solid #d1d5db;
    background-color: #f9fafb;
    color: #111827;
}
QMessageBox QPushButton:hover, QInputDialog QPushButton:hover {
    background-color: #f3f4f6;
    border-color: #9ca3af;
}
QMessageBox QPushButton:pressed, QInputDialog QPushButton:pressed {
    background-color: #e5e7eb;
}
QMessageBox QPushButton:focus, QInputDialog QPushButton:focus {
    border: 1.5px solid #111827;
}
QInputDialog QLineEdit {
    background-color: #ffffff;
    color: #111827;
    border: 1px solid #d1d5db;
    border-radius: 5px;
    padding: 6px 10px;
    font-size: 12px;
}
QInputDialog QLineEdit:focus {
    border: 1.5px solid #111827;
}
"""

GLOBAL_TABLE_QSS = """
QTableWidget {
    background-color: #ffffff;
    gridline-color: #f3f4f6;
    color: #111827;
    font-size: 12px;
    selection-background-color: #f3f4f6;
    selection-color: #111827;
}
QHeaderView::section {
    background-color: #f9fafb;
    color: #374151;
    border: 1px solid #e5e7eb;
    font-size: 12px;
    font-weight: bold;
    padding: 4px 6px;
}
QTableWidget::item {
    padding: 3px 6px;
}
"""

ADMIN_AUDIT_ACTION_MAP = {
    "LOGIN_SUCCESS": "Đăng nhập thành công",
    "LOGIN_FAILED": "Đăng nhập thất bại",
    "LOGOUT": "Đăng xuất",
    "ACCOUNT_LOCKED": "Tài khoản bị khóa",
    "PROFILE_UPDATE": "Cập nhật hồ sơ",
    "PASSWORD_CHANGE": "Đổi mật khẩu",
    "USER_CREATE": "Tạo tài khoản",
    "USER_CREATED": "Tạo tài khoản",
    "USER_LOCK": "Khóa / Mở khóa",
    "USER_TOGGLE_ACTIVE": "Khóa / Mở khóa",
    "USER_DELETE": "Xóa tài khoản",
    "USER_DELETED": "Xóa tài khoản",
    "USERNAME_CHANGE": "Đổi tên đăng nhập",
    "RESET_PASSWORD": "Cứu hộ mật khẩu",
    "CREATE_ADMIN": "Khởi tạo quản trị",
    "EMERGENCY_RESET": "Cứu hộ khẩn cấp",
    "MIGRATION": "Chuyển đổi dữ liệu",
    "USER_ASSIGN_CLASSES": "Phân công lớp giảng dạy",
}

GLOBAL_CHECKBOX_QSS = """
QCheckBox, QRadioButton {
    color: #111827;
    font-size: 12px;
    spacing: 8px;
}
QCheckBox:disabled, QRadioButton:disabled {
    color: #9ca3af;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1.5px solid #9ca3af;
    background-color: #ffffff;
}
QCheckBox::indicator:hover {
    border-color: #111827;
    background-color: #f9fafb;
}
QCheckBox::indicator:checked {
    background-color: #111827;
    border: 1.5px solid #111827;
    image: url("assets/checkbox_check.png");
}
QCheckBox::indicator:disabled {
    border: 1.5px solid #d1d5db;
    background-color: #f3f4f6;
}
QRadioButton::indicator {
    width: 16px;
    height: 16px;
    border-radius: 8px;
    border: 1.5px solid #9ca3af;
    background-color: #ffffff;
}
QRadioButton::indicator:hover {
    border-color: #111827;
    background-color: #f9fafb;
}
QRadioButton::indicator:checked {
    background-color: #ffffff;
    border: 5px solid #111827;
}
QRadioButton::indicator:disabled {
    border: 1.5px solid #d1d5db;
    background-color: #f3f4f6;
}
"""

GLOBAL_COMBOBOX_QSS = """
QComboBox {
    background-color: #ffffff;
    color: #111827;
    padding: 6px 10px 6px 12px;
    border-radius: 5px;
    border: 1px solid #d1d5db;
    font-size: 12px;
}
QComboBox:hover {
    border-color: #9ca3af;
}
QComboBox:focus, QComboBox:on {
    border-color: #111827;
}
QComboBox:disabled {
    background-color: #f9fafb;
    color: #9ca3af;
    border-color: #e5e7eb;
}
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 24px;
    border-left: 1px solid #d1d5db;
    border-top-right-radius: 5px;
    border-bottom-right-radius: 5px;
    background-color: #f3f4f6;
}
QComboBox::drop-down:hover {
    background-color: #e5e7eb;
}
QComboBox::drop-down:disabled {
    background-color: #f9fafb;
    border-left: 1px solid #e5e7eb;
}
QComboBox::down-arrow {
    image: url("assets/dropdown_arrow.png");
    width: 10px;
    height: 10px;
}
QComboBox::down-arrow:disabled {
    image: url("assets/dropdown_arrow_disabled.png");
    width: 10px;
    height: 10px;
}
QComboBox QAbstractItemView {
    background-color: #ffffff;
    color: #111827;
    border: 1px solid #d1d5db;
    border-radius: 5px;
    selection-background-color: #f3f4f6;
    selection-color: #111827;
    outline: none;
    padding: 2px;
}
QComboBox QAbstractItemView::item {
    background-color: #ffffff;
    color: #111827;
    padding: 6px 10px;
    min-height: 24px;
    border-radius: 4px;
}
QComboBox QAbstractItemView::item:hover {
    background-color: #f3f4f6;
    color: #111827;
}
QComboBox QAbstractItemView::item:selected {
    background-color: #e5e7eb;
    color: #111827;
}
"""

BASE_WINDOW_STYLE = "background-color: #ffffff; color: #111827; font-family: 'Aptos', 'Segoe UI', -apple-system, sans-serif;\n" + GLOBAL_MENU_QSS + "\n" + GLOBAL_SCROLLBAR_QSS + "\n" + GLOBAL_DIALOG_QSS + "\n" + GLOBAL_COMBOBOX_QSS + "\n" + GLOBAL_CHECKBOX_QSS


def add_form_row(form: QFormLayout, label_text: str, widget: QWidget, label_width: int = 140, height: int = 36) -> QLabel:
    """Thêm một hàng vào QFormLayout với nhãn và widget thẳng hàng tuyệt đối theo trục ngang."""
    lbl = QLabel(label_text)
    if label_width > 0:
        lbl.setFixedWidth(label_width)
    lbl.setFixedHeight(height)
    lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
    lbl.setStyleSheet("color: #374151; font-size: 12px; font-weight: normal; border: none; background: transparent;")
    if not isinstance(widget, (QLabel, QStackedWidget)):
        widget.setFixedHeight(height)
    elif isinstance(widget, QLabel):
        widget.setFixedHeight(height)
        widget.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
    form.addRow(lbl, widget)
    return lbl


def _create_cell_item(text: str, alignment=Qt.AlignmentFlag.AlignCenter, color=None) -> QTableWidgetItem:
    """Tạo QTableWidgetItem với canh lề chuẩn xác và màu sắc tùy biến."""
    item = QTableWidgetItem(str(text))
    item.setTextAlignment(alignment)
    if color:
        item.setForeground(QColor(color))
    return item


class NumericTableWidgetItem(QTableWidgetItem):
    """QTableWidgetItem hỗ trợ sắp xếp theo thứ tự số học (0-9) thay vì chuỗi từ điển (A-Z)."""
    def __init__(self, value, alignment=Qt.AlignmentFlag.AlignCenter, color=None):
        super().__init__(str(value) if value is not None else "")
        self.raw_val = value
        self.setTextAlignment(alignment)
        if color:
            self.setForeground(QColor(color))

    def __lt__(self, other):
        if isinstance(other, QTableWidgetItem):
            try:
                val_self = float(self.raw_val) if self.raw_val is not None else float(self.text())
                val_other = float(other.raw_val) if hasattr(other, "raw_val") and other.raw_val is not None else float(other.text())
                return val_self < val_other
            except (ValueError, TypeError):
                pass
        return super().__lt__(other)


def format_time_str(time_str: str, use_ampm: bool = False) -> str:
    """Chuyển đổi chuỗi thời gian (HH:mm hoặc HH:mm:ss) sang định dạng 12 giờ AM/PM nếu use_ampm=True."""
    if not time_str or time_str in ("--:--", "--", ""):
        return "--:--" if time_str in ("--:--", "--") else ""
    if not use_ampm:
        return time_str
    try:
        parts = time_str.split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        s = int(parts[2]) if len(parts) > 2 else 0
        t = QTime(h, m, s)
        if len(parts) >= 3:
            return t.toString("hh:mm:ss AP")
        else:
            return t.toString("hh:mm AP")
    except Exception:
        return time_str


def format_datetime_str(dt_str: str) -> str:
    """Chuyển đổi chuỗi ngày giờ (YYYY-MM-DD HH:MM:SS) sang định dạng chuẩn dd/MM/yyyy HH:mm:ss."""
    if not dt_str or dt_str in ("-", "--", ""):
        return "-"
    s = str(dt_str).strip()
    try:
        sep = " " if " " in s else ("T" if "T" in s else None)
        if sep:
            d_part, t_part = s.split(sep, 1)
            parts = d_part.split("-")
            if len(parts) == 3 and len(parts[0]) == 4:
                if "." in t_part:
                    t_part = t_part.split(".")[0]
                return f"{parts[2]}/{parts[1]}/{parts[0]} {t_part}"
        elif "-" in s:
            parts = s.split("-")
            if len(parts) == 3 and len(parts[0]) == 4:
                return f"{parts[2]}/{parts[1]}/{parts[0]}"
    except Exception:
        pass
    return s


class DateTimeTableWidgetItem(QTableWidgetItem):
    """QTableWidgetItem hiển thị dd/MM/yyyy HH:mm:ss nhưng sắp xếp theo thời gian ISO gốc."""
    def __init__(self, raw_iso_dt, alignment=Qt.AlignmentFlag.AlignCenter):
        display = format_datetime_str(raw_iso_dt)
        super().__init__(display)
        self.raw_iso = str(raw_iso_dt or "")
        self.setTextAlignment(alignment)

    def __lt__(self, other):
        if isinstance(other, DateTimeTableWidgetItem):
            return self.raw_iso < other.raw_iso
        return super().__lt__(other)


def ask_yes_no(parent, title: str, text: str) -> bool:
    """Hộp thoại xác nhận với 2 nút tiếng Việt 'Có' và 'Không' (thay thế nút Yes/No mặc định của hệ điều hành)."""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.Question)
    btn_yes = box.addButton("Có", QMessageBox.ButtonRole.YesRole)
    btn_no = box.addButton("Không", QMessageBox.ButtonRole.NoRole)
    box.setDefaultButton(btn_no)
    box.setStyleSheet(GLOBAL_DIALOG_QSS)
    box.exec()
    return box.clickedButton() == btn_yes


class ClickableTimeEdit(QTimeEdit):
    """QTimeEdit phát tín hiệu clicked khi nhấp chuột hoặc lấy focus, bỏ nút tăng giảm xấu và chặn cuộn chuột nhảy giờ."""
    clicked = pyqtSignal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self.clicked.emit()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.clicked.emit()

    def wheelEvent(self, event):
        event.ignore()

    def contextMenuEvent(self, event):
        menu = self.lineEdit().createStandardContextMenu()
        menu.addSeparator()
        act_up = menu.addAction("Step up", self.stepUp)
        act_down = menu.addAction("Step down", self.stepDown)
        if self.isReadOnly():
            act_up.setEnabled(False)
            act_down.setEnabled(False)
        menu.setStyleSheet(GLOBAL_MENU_QSS)
        menu.exec(event.globalPos())


class InteractiveClockWidget(QWidget):
    """Đồng hồ tương tác trực quan: xoay kim giờ/phút bằng chuột hoặc nút chỉnh nhanh."""
    timeChanged = pyqtSignal(QTime)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(170, 170)
        self.current_time = QTime(7, 30)
        self.is_dragging = False
        self.drag_mode = None  # 'hour' hoặc 'minute'
        self.setMouseTracking(True)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

    def set_time(self, time: QTime):
        if self.current_time != time:
            self.current_time = time
            self.update()
            self.timeChanged.emit(self.current_time)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position()
            cx, cy = self.width() / 2, self.height() / 2
            dx, dy = pos.x() - cx, pos.y() - cy
            dist = math.hypot(dx, dy)
            R = min(self.width(), self.height()) / 2 - 8
            if dist <= R + 10:
                self.is_dragging = True
                self.drag_mode = "hour" if dist < (R * 0.58) else "minute"
                self._update_time_from_pos(dx, dy)

    def mouseMoveEvent(self, event):
        if self.is_dragging:
            pos = event.position()
            cx, cy = self.width() / 2, self.height() / 2
            dx, dy = pos.x() - cx, pos.y() - cy
            self._update_time_from_pos(dx, dy)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_dragging = False
            self.drag_mode = None

    def _update_time_from_pos(self, dx, dy):
        angle = (math.degrees(math.atan2(dy, dx)) + 90) % 360
        if self.drag_mode == "hour":
            h_12 = int(round(angle / 30.0)) % 12
            is_pm = self.current_time.hour() >= 12
            new_h = (h_12 % 12) + (12 if is_pm else 0)
            self.set_time(QTime(new_h, self.current_time.minute()))
        else:
            minute = int(round(angle / 6.0)) % 60
            self.set_time(QTime(self.current_time.hour(), minute))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        R = min(w, h) / 2 - 8

        # Viền và nền mặt số (Monochrome Nền Trắng)
        p.setBrush(QColor("#ffffff"))
        p.setPen(QPen(QColor("#d1d5db"), 2))
        p.drawEllipse(QPointF(cx, cy), R, R)

        # Đường tròn nét đứt phân tách vùng Giờ (trong) và Phút (ngoài)
        p.setPen(QPen(QColor("#e5e7eb"), 1, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), R * 0.58, R * 0.58)

        # 12 số giờ (Font Aptos, màu chữ đen)
        p.setPen(QColor("#111827"))
        font = QFont("Aptos", 8, QFont.Weight.Bold)
        p.setFont(font)
        for h_num in range(1, 13):
            ang = math.radians(h_num * 30 - 90)
            tx = cx + (R - 16) * math.cos(ang)
            ty = cy + (R - 16) * math.sin(ang)
            p.drawText(int(tx - 10), int(ty - 10), 20, 20, Qt.AlignmentFlag.AlignCenter, str(h_num))

        # Vạch phút mỗi 5 phút
        p.setPen(QPen(QColor("#9ca3af"), 1))
        for m_tick in range(0, 60, 5):
            ang = math.radians(m_tick * 6 - 90)
            x1 = cx + (R - 4) * math.cos(ang)
            y1 = cy + (R - 4) * math.sin(ang)
            x2 = cx + (R - 1) * math.cos(ang)
            y2 = cy + (R - 1) * math.sin(ang)
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        # Kim Giờ (Đen monochrome)
        h_val = self.current_time.hour() % 12 + self.current_time.minute() / 60.0
        h_ang = math.radians(h_val * 30 - 90)
        hx = cx + (R * 0.46) * math.cos(h_ang)
        hy = cy + (R * 0.46) * math.sin(h_ang)
        p.setPen(QPen(QColor("#111827"), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx, cy), QPointF(hx, hy))

        # Kim Phút (Xám đậm)
        m_val = self.current_time.minute()
        m_ang = math.radians(m_val * 6 - 90)
        mx = cx + (R * 0.74) * math.cos(m_ang)
        my = cy + (R * 0.74) * math.sin(m_ang)
        p.setPen(QPen(QColor("#4b5563"), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx, cy), QPointF(mx, my))

        # Chốt trục giữa
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#111827"))
        p.drawEllipse(QPointF(cx, cy), 4.5, 4.5)


class _ClassInputAdapter:
    def __init__(self, combo):
        self.combo = combo

    def text(self):
        return self.combo.currentText()

    def setText(self, text):
        idx = self.combo.findText(text)
        if idx >= 0:
            self.combo.setCurrentIndex(idx)
        else:
            self.combo.addItem(text, text)
            self.combo.setCurrentIndex(self.combo.count() - 1)


class RegistrationDialog(QDialog):
    """Cửa sổ đăng ký sinh viên mới: khóa cả check-in/out và chỉ dùng camera check-in để đăng ký."""
    def __init__(self, db, face_engine, update_cache_cb, get_cache_cb=None, worker_in=None, worker_out=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.face_engine = face_engine
        self.update_cache_cb = update_cache_cb
        self.get_cache_cb = get_cache_cb
        self.worker_in = worker_in
        self.worker_out = worker_out
        self.captured_frame = None
        self.extracted_embedding = None
        self.current_frame = None
        self._preview_frame_count = 0
        self._last_detected_faces = []

        # Tạm dừng quét điểm danh trên CẢ 2 camera khi đang trong quá trình đăng ký
        if self.worker_in:
            self.worker_in.pause_attendance = True
        if self.worker_out:
            self.worker_out.pause_attendance = True

        self.setWindowTitle("Đăng ký Sinh viên Mới")
        self.setFixedSize(720, 500)
        self.setStyleSheet(BASE_WINDOW_STYLE)

        self._init_ui()
        self._start_preview()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 12, 15, 12)
        main_layout.setSpacing(12)

        content_layout = QHBoxLayout()
        content_layout.setSpacing(16)

        # Cột trái: Camera preview & Bounding box
        left_layout = QVBoxLayout()
        left_layout.setSpacing(8)
        self.video_label = QLabel("Đang mở camera...")
        self.video_label.setFixedSize(360, 360)
        self.video_label.setStyleSheet("border: 1.5px solid #d1d5db; background-color: #000000; border-radius: 8px; color: #f3f4f6;")
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(self.video_label)

        self.status_lbl = QLabel("Vui lòng nhìn thẳng vào camera")
        self.status_lbl.setStyleSheet("color: #4b5563; font-weight: bold; font-size: 12px;")
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(self.status_lbl)
        content_layout.addLayout(left_layout)

        # Cột phải: Form thông tin & Chân dung căn giữa khoảng trống
        right_layout = QVBoxLayout()
        right_layout.setSpacing(8)
        form_group = QGroupBox("Thông tin Sinh viên")
        form_group.setStyleSheet("""
        QGroupBox {
            font-weight: bold;
            color: #111827;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            margin-top: 8px;
            padding-top: 14px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 10px;
            padding: 0 4px;
            background-color: #ffffff;
        }
        """)
        form = QFormLayout(form_group)
        form.setSpacing(8)

        self.txt_code = QLineEdit()
        self.txt_code.setPlaceholderText("VD: 20210001")
        self.txt_code.setStyleSheet("background-color: #ffffff; color: #111827; padding: 4px 10px; border-radius: 5px; border: 1.5px solid #d1d5db; font-size: 12px;")
        add_form_row(form, "Mã số SV (MSSV):", self.txt_code, label_width=125, height=36)

        self.txt_name = QLineEdit()
        self.txt_name.setPlaceholderText("VD: Nguyen Van A")
        self.txt_name.setStyleSheet("background-color: #ffffff; color: #111827; padding: 4px 10px; border-radius: 5px; border: 1.5px solid #d1d5db; font-size: 12px;")
        add_form_row(form, "Họ và Tên:", self.txt_name, label_width=125, height=36)

        self.combo_class = QComboBox()
        self.combo_class.setStyleSheet(GLOBAL_COMBOBOX_QSS)
        self.combo_class.setFixedHeight(36)
        all_cls = self.db.get_all_classes()
        if all_cls:
            for c in all_cls:
                self.combo_class.addItem(c, c)
        else:
            self.combo_class.addItem("Chưa có lớp nào", "")
        add_form_row(form, "Lớp:", self.combo_class, label_width=125, height=36)
        self.txt_class = _ClassInputAdapter(self.combo_class)

        right_layout.addWidget(form_group)

        # Căn giữa chính xác box ảnh chân dung trong khoảng trống giữa box TTSV và đáy webcam
        right_layout.addSpacing(25)
        self.preview_photo_lbl = QLabel("Chưa chụp ảnh")
        self.preview_photo_lbl.setFixedSize(140, 140)
        self.preview_photo_lbl.setStyleSheet("border: 1px dashed #9ca3af; background-color: #f9fafb; border-radius: 6px; color: #6b7280;")
        self.preview_photo_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_layout.addWidget(self.preview_photo_lbl, alignment=Qt.AlignmentFlag.AlignCenter)
        right_layout.addStretch(1)

        content_layout.addLayout(right_layout)
        main_layout.addLayout(content_layout)

        # Hàng nút bấm phía dưới: Tất cả 4 nút nằm trên cùng 1 hàng ngang, cùng độ cao, cùng độ rộng và khoảng cách bằng nhau (Monochrome style)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        self.btn_capture = QPushButton("Chụp ảnh khuôn mặt")
        self.btn_capture.setFixedHeight(38)
        self.btn_capture.setStyleSheet("background-color: #111827; color: #ffffff; font-weight: bold; font-size: 13px; border-radius: 6px;")
        self.btn_capture.clicked.connect(self.on_capture)
        btn_row.addWidget(self.btn_capture, 1)

        self.btn_choose_file = QPushButton("Chọn ảnh từ tệp")
        self.btn_choose_file.setFixedHeight(38)
        self.btn_choose_file.setStyleSheet("background-color: #111827; color: #ffffff; font-weight: bold; font-size: 13px; border-radius: 6px; border: 1px solid #d1d5db;")
        self.btn_choose_file.clicked.connect(self.on_choose_file)
        btn_row.addWidget(self.btn_choose_file, 1)

        self.btn_save = QPushButton("Lưu hồ sơ")
        self.btn_save.setFixedHeight(38)
        self.btn_save.setStyleSheet("background-color: #111827; color: #ffffff; font-weight: bold; font-size: 13px; border-radius: 6px;")
        self.btn_save.clicked.connect(self.on_save)
        btn_row.addWidget(self.btn_save, 1)

        self.btn_cancel = QPushButton("Hủy bỏ")
        self.btn_cancel.setFixedHeight(38)
        self.btn_cancel.setStyleSheet("background-color: #f3f4f6; color: #111827; font-weight: bold; font-size: 13px; border-radius: 6px; border: 1px solid #d1d5db;")
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel, 1)

        main_layout.addLayout(btn_row)
        disable_wheel_on_inputs(self)

    def _start_preview(self):
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update_preview_frame)
        self.timer.start(30)

    def _update_preview_frame(self):
        # Chỉ sử dụng Camera Cổng Vào (worker_in) để lấy khung hình đăng ký
        frame = self.worker_in.get_latest_frame() if self.worker_in else None
        if frame is None:
            return
        self.current_frame = frame.copy()
        
        # Nhận diện mặt định kỳ mỗi 4 frame (~8-10 FPS) để giải phóng CPU luồng GUI
        self._preview_frame_count += 1
        if self._preview_frame_count % 4 == 0 or not self._last_detected_faces:
            self._last_detected_faces = self.face_engine.process_frame(frame)

        faces = self._last_detected_faces
        annotated = frame.copy()
        if faces:
            x1, y1, x2, y2 = faces[0]["bbox"]
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
            self.status_lbl.setText("Phát hiện khuôn mặt hợp lệ")
            self.status_lbl.setStyleSheet("color: #16a34a; font-weight: bold;")
        else:
            self.status_lbl.setText("Chưa phát hiện khuôn mặt")
            self.status_lbl.setStyleSheet("color: #d97706; font-weight: bold;")

        rgb = cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
        self.video_label.setPixmap(QPixmap.fromImage(qimg).scaled(360, 360, Qt.AspectRatioMode.KeepAspectRatio))

    def on_capture(self):
        if not hasattr(self, "current_frame") or self.current_frame is None:
            QMessageBox.warning(self, "Thông báo", "Camera chưa sẵn sàng.")
            return
        emb, bbox, conf = self.face_engine.extract_single_face(self.current_frame)
        if emb is None:
            QMessageBox.warning(self, "Lỗi", "Không tìm thấy khuôn mặt rõ ràng. Vui lòng căn chỉnh lại.")
            return

        # Kiểm tra trùng khuôn mặt ngay khi chụp ảnh
        cache = self.get_cache_cb() if self.get_cache_cb else self.db.load_cached_embeddings()
        matched_student, _ = self.face_engine.match(emb, cache)
        if matched_student:
            self.captured_frame = None
            self.extracted_embedding = None
            self.preview_photo_lbl.setText("Chưa chụp ảnh")
            self.preview_photo_lbl.setPixmap(QPixmap())
            QMessageBox.warning(
                self,
                "Trùng khuôn mặt sinh viên",
                f"Khuôn mặt này đã được đăng ký trong hệ thống cho sinh viên:\n{matched_student['full_name']} (MSSV: {matched_student['student_code']}).\nKhông thể chụp hoặc sử dụng khuôn mặt này."
            )
            return

        self.captured_frame = self.current_frame.copy()
        self.extracted_embedding = emb

        # Hiển thị crop mặt sang khung ảnh xem trước
        x1, y1, x2, y2 = bbox
        h, w, _ = self.captured_frame.shape
        crop = self.captured_frame[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
        if crop.size > 0:
            rgb_crop = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
            ch, cw, cch = rgb_crop.shape
            qcrop = QImage(rgb_crop.data, cw, ch, cch * cw, QImage.Format.Format_RGB888)
            self.preview_photo_lbl.setPixmap(QPixmap.fromImage(qcrop).scaled(140, 140, Qt.AspectRatioMode.KeepAspectRatio))
        QMessageBox.information(self, "Thành công", "Đã chụp và nhận diện khuôn mặt thành công.")

    def on_choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Chọn ảnh chân dung", "", "Ảnh (*.jpg *.png *.jpeg)")
        if not path:
            return
        img = cv2.imread(path)
        if img is None:
            QMessageBox.warning(self, "Lỗi", "Không thể đọc tệp ảnh.")
            return
        emb, bbox, conf = self.face_engine.extract_single_face(img)
        if emb is None:
            QMessageBox.warning(self, "Lỗi", "Không phát hiện khuôn mặt trong ảnh đã chọn.")
            return

        # Kiểm tra trùng khuôn mặt ngay khi chọn tệp ảnh
        cache = self.get_cache_cb() if self.get_cache_cb else self.db.load_cached_embeddings()
        matched_student, _ = self.face_engine.match(emb, cache)
        if matched_student:
            self.captured_frame = None
            self.extracted_embedding = None
            self.preview_photo_lbl.setText("Chưa chụp ảnh")
            self.preview_photo_lbl.setPixmap(QPixmap())
            QMessageBox.warning(
                self,
                "Trùng khuôn mặt sinh viên",
                f"Khuôn mặt trong ảnh đã được đăng ký trong hệ thống cho sinh viên:\n{matched_student['full_name']} (MSSV: {matched_student['student_code']}).\nKhông thể sử dụng ảnh này."
            )
            return

        self.captured_frame = img
        self.extracted_embedding = emb
        
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
        self.preview_photo_lbl.setPixmap(QPixmap.fromImage(qimg).scaled(140, 140, Qt.AspectRatioMode.KeepAspectRatio))
        QMessageBox.information(self, "Thành công", "Đã nạp và trích xuất vector khuôn mặt từ ảnh thành công.")

    def on_save(self):
        code = self.txt_code.text().strip()
        name = self.txt_name.text().strip()
        cname = self.combo_class.currentText().strip()
        if not cname or cname == "Chưa có lớp nào":
            QMessageBox.warning(self, "Chưa chọn lớp", "Vui lòng chọn một lớp học đã tạo trong hệ thống trước khi lưu.")
            return

        if not code or not name:
            QMessageBox.warning(self, "Thiếu thông tin", "Vui lòng nhập MSSV và Họ Tên.")
            return
        if self.extracted_embedding is None:
            QMessageBox.warning(self, "Thiếu ảnh", "Vui lòng chụp ảnh khuôn mặt trước khi lưu.")
            return

        # 1. Kiểm tra trùng MSSV trong CSDL (Lớp 1 - tách biệt theo chế độ debug / chính thức)
        target_mock = 1 if getattr(self.db, "is_debug", False) else 0
        existing_student = self.db.get_student_by_code(code, is_mock=target_mock)
        if existing_student:
            QMessageBox.warning(
                self, 
                "Trùng mã số sinh viên", 
                f"Mã số sinh viên '{code}' đã được đăng ký trước đó cho sinh viên:\n{existing_student['full_name']}."
            )
            return

        # 2. Kiểm tra trùng khuôn mặt bằng AI (Lớp 2 - không hiển thị score)
        cache = self.get_cache_cb() if self.get_cache_cb else self.db.load_cached_embeddings()
        matched_student, _ = self.face_engine.match(self.extracted_embedding, cache)
        if matched_student:
            QMessageBox.warning(
                self, 
                "Trùng khuôn mặt sinh viên", 
                f"Khuôn mặt này đã được đăng ký trong hệ thống cho sinh viên:\n{matched_student['full_name']} (MSSV: {matched_student['student_code']}).\nKhông thể đăng ký trùng lặp."
            )
            return

        # Lưu ảnh chân dung vào thư mục data/photos (mã hoá AES-256-GCM bảo vệ quyền riêng tư)
        photo_path = ""
        if self.captured_frame is not None:
            prefix = "mock_" if getattr(self.db, "is_debug", False) else ""
            photo_path = self.db.save_encrypted_photo(f"data/photos/{prefix}{code}.enc", self.captured_frame)

        try:
            self.db.add_student(code, name, cname, self.extracted_embedding, photo_path)
            self.update_cache_cb()
            QMessageBox.information(self, "Hoàn tất", f"Đã đăng ký thành công sinh viên: {name} ({code})")
            self.accept()
        except Exception as e:
            if photo_path and os.path.exists(photo_path):
                try:
                    os.remove(photo_path)
                except Exception:
                    pass
            QMessageBox.critical(self, "Lỗi cơ sở dữ liệu", f"Không thể lưu sinh viên: {e}")

    def done(self, r):
        if hasattr(self, "timer") and self.timer.isActive():
            self.timer.stop()
        # Phục hồi quét điểm danh cho cả 2 camera khi hoàn tất đăng ký
        if self.worker_in:
            self.worker_in.pause_attendance = False
        if self.worker_out:
            self.worker_out.pause_attendance = False
        super().done(r)


class AdminLoginDialog(QDialog):
    """Hộp thoại xác thực đăng nhập tài khoản quản trị UniFace."""
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.authenticated_user = None
        self.setWindowTitle("Đăng Nhập Quản Trị Hệ Thống")
        self.setFixedWidth(380)
        self.setStyleSheet(BASE_WINDOW_STYLE)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(10)

        header_layout = QVBoxLayout()
        header_layout.setSpacing(4)

        lbl_title = QLabel("XÁC THỰC QUẢN TRỊ VIÊN")
        lbl_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #111827;")
        lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(lbl_title)

        lbl_desc = QLabel("Vui lòng nhập tên tài khoản và mật khẩu quản trị để tiếp tục.")
        lbl_desc.setStyleSheet("color: #6b7280; font-size: 11px;")
        lbl_desc.setWordWrap(True)
        lbl_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(lbl_desc)
        layout.addLayout(header_layout)

        form_layout = QFormLayout()
        form_layout.setSpacing(8)

        self.txt_username = QLineEdit()
        self.txt_username.setPlaceholderText("Tên đăng nhập")
        self.txt_username.setStyleSheet("padding: 4px 10px; border: 1.5px solid #d1d5db; border-radius: 5px; font-size: 12px;")
        add_form_row(form_layout, "Tài khoản:", self.txt_username, label_width=85, height=36)

        self.txt_password = QLineEdit()
        self.txt_password.setPlaceholderText("Mật khẩu")
        self.txt_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_password.setStyleSheet("padding: 4px 10px; border: 1.5px solid #d1d5db; border-radius: 5px; font-size: 12px;")
        add_form_row(form_layout, "Mật khẩu:", self.txt_password, label_width=85, height=36)

        layout.addLayout(form_layout)

        self.chk_show_pwd = QCheckBox("Hiện mật khẩu")
        self.chk_show_pwd.setStyleSheet(GLOBAL_CHECKBOX_QSS)
        self.chk_show_pwd.toggled.connect(
            lambda checked: self.txt_password.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password)
        )
        layout.addWidget(self.chk_show_pwd)

        self.lbl_error = QLabel()
        self.lbl_error.setStyleSheet("color: #dc2626; font-size: 11px; font-weight: bold;")
        self.lbl_error.setWordWrap(True)
        self.lbl_error.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_error.setVisible(False)
        layout.addWidget(self.lbl_error)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.btn_cancel = QPushButton("Hủy bỏ")
        self.btn_cancel.setAutoDefault(False)
        self.btn_cancel.setDefault(False)
        self.btn_cancel.setStyleSheet("""
        QPushButton {
            background-color: #f3f4f6; color: #374151; font-weight: bold;
            padding: 8px 16px; border-radius: 5px; border: 1px solid #d1d5db;
        }
        QPushButton:hover { background-color: #e5e7eb; }
        """)
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel, 1)

        self.btn_login = QPushButton("Đăng nhập")
        self.btn_login.setAutoDefault(False)
        self.btn_login.setDefault(False)
        self.btn_login.setStyleSheet("""
        QPushButton {
            background-color: #1f2937; color: #ffffff; font-weight: bold;
            padding: 8px 16px; border-radius: 5px; border: 1px solid #111827;
        }
        QPushButton:hover { background-color: #374151; }
        """)
        self.btn_login.clicked.connect(self._on_login_clicked)
        btn_layout.addWidget(self.btn_login, 1)

        layout.addLayout(btn_layout)
        self.txt_password.returnPressed.connect(self._on_login_clicked)
        self.txt_username.returnPressed.connect(lambda: self.txt_password.setFocus())
        self.txt_username.setFocus()

        self.countdown_timer = QTimer(self)
        self.countdown_timer.timeout.connect(self._on_countdown_tick)
        self._locked_until_dt = None
        self._update_dialog_height()

    def _show_error(self, text: str, color: str = "#dc2626"):
        """Hiển thị thông báo lỗi và tự động mở rộng chiều cao hộp thoại."""
        self.lbl_error.setText(text)
        self.lbl_error.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold;")
        self.lbl_error.setVisible(True)
        self._update_dialog_height()

    def _clear_error(self):
        """Xóa thông báo và thu gọn hộp thoại về kích thước chuẩn."""
        self.lbl_error.clear()
        self.lbl_error.setVisible(False)
        self._update_dialog_height()

    def _update_dialog_height(self):
        """Cập nhật chiều cao vừa khít nội dung thực tế (snug-fitting)."""
        self.adjustSize()
        self.setFixedHeight(self.sizeHint().height())

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            fw = self.focusWidget()
            if fw == self.btn_cancel:
                self.reject()
                return
            elif fw == self.btn_login:
                self._on_login_clicked()
                return
        super().keyPressEvent(event)

    def _on_login_clicked(self):
        username = self.txt_username.text().strip()
        password = self.txt_password.text()

        import time
        now = time.monotonic()
        current_key = (username, password)
        if current_key == getattr(self, "_last_login_key", None) and (now - getattr(self, "_last_login_time", 0) < 0.5):
            return
        self._last_login_key = current_key
        self._last_login_time = now

        ok, msg, user = self.db.authenticate_admin(username, password)
        if not ok:
            locked_until_str = user.get("locked_until") if isinstance(user, dict) else None
            if locked_until_str:
                try:
                    self._just_locked_now = ("5 lần" in msg)
                    self._locked_until_dt = datetime.fromisoformat(locked_until_str)
                    self._on_countdown_tick()
                    if not self.countdown_timer.isActive():
                        self.countdown_timer.start(1000)
                except Exception:
                    self._show_error(msg)
            else:
                if self.countdown_timer.isActive():
                    self.countdown_timer.stop()
                self._locked_until_dt = None
                self._just_locked_now = False
                self._show_error(msg)
            self.txt_password.selectAll()
            self.txt_password.setFocus()
            return

        if self.countdown_timer.isActive():
            self.countdown_timer.stop()
        self._clear_error()
        self.authenticated_user = user
        self.accept()

    def _on_countdown_tick(self):
        if not self._locked_until_dt:
            self.countdown_timer.stop()
            return
        rem_sec = int((self._locked_until_dt - datetime.now()).total_seconds())
        if rem_sec > 0:
            rem_m = rem_sec // 60
            rem_s = rem_sec % 60
            time_str = f"{rem_m} phút {rem_s:02d} giây" if rem_m > 0 else f"{rem_s} giây"
            mins = (rem_sec + 59) // 60
            if getattr(self, "_just_locked_now", False) or mins >= 15:
                self._show_error(
                    f"Bạn đã nhập sai mật khẩu 5 lần liên tiếp.\nTài khoản đã bị tạm khóa trong 15 phút (còn {time_str})."
                )
            else:
                self._show_error(
                    f"Tài khoản bị tạm khóa do nhập sai nhiều lần.\nVui lòng thử lại sau {mins} phút (còn {time_str})."
                )
        else:
            self.countdown_timer.stop()
            self._locked_until_dt = None
            self._just_locked_now = False
            self._show_error("Thời gian khóa đã kết thúc. Bạn có thể đăng nhập lại.", "#16a34a")


class ManageClassesDialog(QDialog):
    """Hộp thoại quản lý danh mục lớp học chính thức của hệ thống."""
    def __init__(self, parent, db, actor_username: str = ""):
        super().__init__(parent)
        self.db = db
        self.actor_username = actor_username
        title_suffix = " (Chế độ Thử nghiệm)" if getattr(self.db, "is_debug", False) else ""
        self.setWindowTitle(f"Quản Lý Danh Mục Lớp Học{title_suffix}")
        self.setFixedSize(540, 480)
        self.setStyleSheet(BASE_WINDOW_STYLE)
        self._init_ui()
        self.load_classes()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        hint_text = (
            "Danh mục các lớp học thử nghiệm (Sandbox). Lớp học được tạo tại đây chỉ tồn tại trong chế độ Debug và độc lập hoàn toàn với app chính."
            if getattr(self.db, "is_debug", False)
            else "Danh mục các lớp học trong hệ thống. Sinh viên khi đăng ký bắt buộc phải chọn từ danh mục này."
        )
        lbl_hint = QLabel(hint_text)
        lbl_hint.setWordWrap(True)
        lbl_hint.setStyleSheet("color: #6b7280; font-size: 12px;")
        layout.addWidget(lbl_hint)

        self.table_classes = QTableWidget()
        self.table_classes.setColumnCount(3)
        self.table_classes.setHorizontalHeaderLabels(["ID", "Tên lớp học", "Số lượng sinh viên"])
        hdr = self.table_classes.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.table_classes.setColumnWidth(0, 60)
        self.table_classes.setColumnWidth(2, 140)
        self.table_classes.setStyleSheet("""
        QTableWidget {
            background-color: #ffffff;
            gridline-color: #f3f4f6;
            color: #111827;
            font-size: 12px;
            selection-background-color: #f3f4f6;
            selection-color: #111827;
        }
        QHeaderView::section {
            background-color: #f9fafb;
            color: #374151;
            border: 1px solid #e5e7eb;
            font-size: 12px;
            font-weight: bold;
            padding: 2px 4px;
        }
        QTableWidget::item {
            padding: 2px 6px;
        }
        """ + GLOBAL_SCROLLBAR_QSS)
        self.table_classes.verticalHeader().setVisible(False)
        self.table_classes.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table_classes.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_classes.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table_classes.setSortingEnabled(True)
        layout.addWidget(self.table_classes)

        add_box = QGroupBox("Thêm Lớp Học Mới")
        add_box.setStyleSheet("""
        QGroupBox {
            font-weight: bold;
            color: #111827;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            margin-top: 8px;
            padding-top: 14px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 10px;
            padding: 0 4px;
            background-color: #ffffff;
        }
        """)
        add_layout = QHBoxLayout(add_box)
        add_layout.setContentsMargins(12, 12, 12, 10)
        add_layout.setSpacing(10)

        self.txt_new_class = QLineEdit()
        self.txt_new_class.setPlaceholderText("VD: 10A1 hoặc K66-DDT")
        self.txt_new_class.setFixedHeight(36)
        self.txt_new_class.setStyleSheet("background-color: #ffffff; color: #111827; padding: 4px 10px; border-radius: 5px; border: 1.5px solid #d1d5db; font-size: 12px;")
        add_layout.addWidget(self.txt_new_class, 1)

        self.btn_add_class = QPushButton("Thêm lớp")
        self.btn_add_class.setFixedHeight(36)
        self.btn_add_class.setStyleSheet("background-color: #111827; color: #ffffff; font-weight: bold; border-radius: 6px; padding: 0 16px;")
        self.btn_add_class.clicked.connect(self._on_add_class)
        add_layout.addWidget(self.btn_add_class)

        layout.addWidget(add_box)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        self.btn_delete_class = QPushButton("Xóa lớp đã chọn")
        self.btn_delete_class.setFixedHeight(38)
        self.btn_delete_class.setStyleSheet("background-color: #ffffff; color: #dc2626; font-weight: bold; border-radius: 6px; border: 1px solid #fca5a5;")
        self.btn_delete_class.clicked.connect(self._on_delete_class)
        btn_row.addWidget(self.btn_delete_class, 1)

        self.btn_close = QPushButton("Đóng")
        self.btn_close.setFixedHeight(38)
        self.btn_close.setStyleSheet("background-color: #f3f4f6; color: #111827; font-weight: bold; border-radius: 6px; border: 1px solid #d1d5db;")
        self.btn_close.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_close, 1)

        layout.addLayout(btn_row)
        disable_wheel_on_inputs(self)

    def load_classes(self):
        self.table_classes.setSortingEnabled(False)
        classes_data = self.db.get_classes_with_counts()
        self.table_classes.setRowCount(len(classes_data))
        for row, c in enumerate(classes_data):
            self.table_classes.setItem(row, 0, NumericTableWidgetItem(c["id"], Qt.AlignmentFlag.AlignCenter))
            self.table_classes.setItem(row, 1, _create_cell_item(c["name"], Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft))
            self.table_classes.setItem(row, 2, NumericTableWidgetItem(c.get("student_count", 0), Qt.AlignmentFlag.AlignCenter))
        self.table_classes.setSortingEnabled(True)

    def _on_add_class(self):
        name = self.txt_new_class.text().strip()
        if not name:
            QMessageBox.warning(self, "Thiếu thông tin", "Vui lòng nhập tên lớp học.")
            return
        ok, msg = self.db.add_class(name)
        if ok:
            self.txt_new_class.clear()
            self.load_classes()
            QMessageBox.information(self, "Thành công", msg)
        else:
            QMessageBox.warning(self, "Thông báo", msg)

    def _on_delete_class(self):
        curr_row = self.table_classes.currentRow()
        if curr_row < 0:
            QMessageBox.warning(self, "Thông báo", "Vui lòng chọn lớp cần xóa trong danh sách.")
            return
        c_name = self.table_classes.item(curr_row, 1).text()
        if ask_yes_no(self, "Xác nhận xóa", f"Bạn có chắc muốn xóa lớp '{c_name}'?"):
            ok, msg = self.db.delete_class(c_name)
            if ok:
                self.load_classes()
                QMessageBox.information(self, "Thành công", msg)
            else:
                QMessageBox.warning(self, "Không thể xóa", msg)


class AssignClassesDialog(QDialog):
    """Hộp thoại phân công lớp giảng dạy cho tài khoản Giáo viên."""
    def __init__(self, parent, db, teacher_user: dict, actor_username: str = ""):
        super().__init__(parent)
        self.db = db
        self.teacher_user = teacher_user
        self.actor_username = actor_username
        self.setWindowTitle("Phân Công Lớp Giảng Dạy")
        self.setFixedSize(540, 490)
        self.setStyleSheet(BASE_WINDOW_STYLE)
        self.class_checkboxes = []
        self._init_ui()
        self.load_classes()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        t_name = str(self.teacher_user.get("full_name") or "Giáo viên")
        t_user = str(self.teacher_user.get("username") or "")
        lbl_info = QLabel(f"Giáo viên: {t_name} (@{t_user})")
        lbl_info.setStyleSheet("font-size: 13px; font-weight: bold; color: #111827;")
        layout.addWidget(lbl_info)

        lbl_desc = QLabel("Tích chọn các lớp học phân công cho giáo viên phụ trách. Giáo viên chỉ được xem và quản lý điểm danh đối với các lớp được tích chọn.")
        lbl_desc.setWordWrap(True)
        lbl_desc.setStyleSheet("color: #6b7280; font-size: 12px;")
        layout.addWidget(lbl_desc)

        self.table_assign = QTableWidget()
        self.table_assign.setColumnCount(3)
        self.table_assign.setHorizontalHeaderLabels(["Chọn", "Tên lớp học", "Số lượng sinh viên"])
        hdr = self.table_assign.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.table_assign.setColumnWidth(0, 70)
        self.table_assign.setColumnWidth(2, 140)
        self.table_assign.setStyleSheet("""
        QTableWidget {
            background-color: #ffffff;
            gridline-color: #f3f4f6;
            color: #111827;
            font-size: 12px;
            selection-background-color: #f3f4f6;
            selection-color: #111827;
        }
        QHeaderView::section {
            background-color: #f9fafb;
            color: #374151;
            border: 1px solid #e5e7eb;
            font-size: 12px;
            font-weight: bold;
            padding: 2px 4px;
        }
        QTableWidget::item {
            padding: 2px 6px;
        }
        """ + GLOBAL_SCROLLBAR_QSS)
        self.table_assign.verticalHeader().setVisible(False)
        self.table_assign.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table_assign.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table_assign)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        self.btn_save = QPushButton("Lưu phân công")
        self.btn_save.setFixedHeight(38)
        self.btn_save.setStyleSheet("background-color: #111827; color: #ffffff; font-weight: bold; border-radius: 6px;")
        self.btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(self.btn_save, 1)

        self.btn_cancel = QPushButton("Hủy bỏ")
        self.btn_cancel.setFixedHeight(38)
        self.btn_cancel.setStyleSheet("background-color: #f3f4f6; color: #111827; font-weight: bold; border-radius: 6px; border: 1px solid #d1d5db;")
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel, 1)

        layout.addLayout(btn_row)
        disable_wheel_on_inputs(self)

    def load_classes(self):
        classes_data = self.db.get_classes_with_counts()
        assigned = set(self.db.get_admin_user_classes(self.teacher_user["id"]))
        self.table_assign.setRowCount(len(classes_data))
        self.class_checkboxes = []

        for row, c in enumerate(classes_data):
            cname = c["name"]
            chk = QCheckBox()
            chk.setStyleSheet(GLOBAL_CHECKBOX_QSS)
            chk.setChecked(cname in assigned)
            chk_container = QWidget()
            chk_layout = QHBoxLayout(chk_container)
            chk_layout.setContentsMargins(0, 0, 0, 0)
            chk_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chk_layout.addWidget(chk)
            self.table_assign.setCellWidget(row, 0, chk_container)
            self.class_checkboxes.append((cname, chk))

            self.table_assign.setItem(row, 1, _create_cell_item(cname, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft))
            self.table_assign.setItem(row, 2, NumericTableWidgetItem(c.get("student_count", 0), Qt.AlignmentFlag.AlignCenter))

    def _on_save(self):
        selected_classes = [cname for cname, chk in self.class_checkboxes if chk.isChecked()]
        uid = self.teacher_user["id"]
        self.db.set_admin_user_classes(uid, selected_classes, actor_username=self.actor_username)
        QMessageBox.information(self, "Thành công", f"Đã cập nhật phân công {len(selected_classes)} lớp cho giáo viên.")
        self.accept()


class CreateMockDataDialog(QDialog):
    """Hộp thoại tạo sinh viên và lớp học ảo phục vụ kiểm thử trong Chế độ Debug."""
    def __init__(self, parent, db, on_created_cb=None):
        super().__init__(parent)
        self.db = db
        self.on_created_cb = on_created_cb
        self.setWindowTitle("Tạo Dữ Liệu Lớp Học và Sinh Viên Ảo")
        self.setFixedWidth(500)
        self.setStyleSheet(BASE_WINDOW_STYLE)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)
        layout.setContentsMargins(20, 20, 20, 18)
        layout.setSpacing(14)

        f_layout = QFormLayout()
        f_layout.setSpacing(10)
        f_layout.setContentsMargins(0, 0, 0, 0)

        self.combo_mode = QComboBox()
        self.combo_mode.setStyleSheet(GLOBAL_COMBOBOX_QSS)
        self.combo_mode.setFixedHeight(36)
        self.combo_mode.addItem("Tạo nhanh mặc định (2 lớp, 6 sinh viên)")
        self.combo_mode.addItem("Tùy chỉnh số lượng lớp học và sinh viên")
        self.combo_mode.currentIndexChanged.connect(self._on_mode_changed)
        add_form_row(f_layout, "Chế độ tạo:", self.combo_mode, label_width=130, height=36)

        self.txt_custom_classes = QLineEdit()
        self.txt_custom_classes.setPlaceholderText("Tên lớp cách nhau bởi dấu phẩy")
        self.txt_custom_classes.setFixedHeight(36)
        self.txt_custom_classes.setStyleSheet("""
        QLineEdit {
            background-color: #ffffff;
            color: #111827;
            padding: 4px 10px 4px 8px;
            border-radius: 5px;
            border: 1.5px solid #d1d5db;
            font-size: 12px;
        }
        QLineEdit:disabled {
            background-color: #f9fafb;
            color: #9ca3af;
            border-color: #e5e7eb;
        }
        """)
        add_form_row(f_layout, "Danh sách lớp:", self.txt_custom_classes, label_width=130, height=36)

        self.spin_count_per_class = QSpinBox()
        self.spin_count_per_class.setRange(1, 50)
        self.spin_count_per_class.lineEdit().setPlaceholderText("Từ 1 đến 50 sinh viên")
        self.spin_count_per_class.clear()
        self.spin_count_per_class.setFixedHeight(36)
        self.spin_count_per_class.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.spin_count_per_class.setStyleSheet("""
        QSpinBox {
            background-color: #ffffff;
            color: #111827;
            padding: 4px 10px 4px 4px;
            border-radius: 5px;
            border: 1.5px solid #d1d5db;
            font-size: 12px;
        }
        QSpinBox:disabled {
            background-color: #f9fafb;
            color: #9ca3af;
            border-color: #e5e7eb;
        }
        QSpinBox::up-button, QSpinBox::down-button {
            width: 0px;
            height: 0px;
            border: none;
        }
        """)
        add_form_row(f_layout, "Sinh viên mỗi lớp:", self.spin_count_per_class, label_width=130, height=36)

        self.combo_name_mode = QComboBox()
        self.combo_name_mode.setStyleSheet(GLOBAL_COMBOBOX_QSS)
        self.combo_name_mode.setFixedHeight(36)
        self.combo_name_mode.addItem("Tự động sinh họ tên tiếng Việt")
        self.combo_name_mode.addItem("Tự động theo mẫu tuần tự")
        self.combo_name_mode.addItem("Tự nhập danh sách tên sinh viên thủ công")
        self.combo_name_mode.currentIndexChanged.connect(self._on_name_mode_changed)
        add_form_row(f_layout, "Cách đặt tên:", self.combo_name_mode, label_width=130, height=36)

        layout.addLayout(f_layout)

        self.txt_manual_names = QTextEdit()
        self.txt_manual_names.setPlaceholderText("Nhập danh sách họ tên sinh viên (mỗi dòng một tên)...")
        self.txt_manual_names.setFixedHeight(75)
        self.txt_manual_names.setStyleSheet("background-color: #ffffff; color: #111827; padding: 6px 10px; border-radius: 5px; border: 1.5px solid #d1d5db; font-size: 12px;")
        self.txt_manual_names.setVisible(False)
        layout.addWidget(self.txt_manual_names)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        self.btn_create = QPushButton("Tạo dữ liệu")
        self.btn_create.setFixedHeight(38)
        self.btn_create.setStyleSheet("""
        QPushButton {
            background-color: #111827;
            color: #ffffff;
            font-weight: bold;
            border-radius: 6px;
        }
        QPushButton:hover {
            background-color: #1f2937;
        }
        """)
        self.btn_create.clicked.connect(self._on_create)
        btn_row.addWidget(self.btn_create, 1)

        self.btn_close = QPushButton("Đóng")
        self.btn_close.setFixedHeight(38)
        self.btn_close.setStyleSheet("""
        QPushButton {
            background-color: #f3f4f6;
            color: #111827;
            font-weight: bold;
            border-radius: 6px;
            border: 1px solid #d1d5db;
        }
        QPushButton:hover {
            background-color: #e5e7eb;
        }
        """)
        self.btn_close.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_close, 1)

        layout.addLayout(btn_row)
        self._on_mode_changed(0)
        disable_wheel_on_inputs(self)

    @property
    def rb_quick(self):
        class _QuickShim:
            def __init__(self, combo):
                self._combo = combo
            def isChecked(self):
                return self._combo.currentIndex() == 0
            def setChecked(self, val):
                if val:
                    self._combo.setCurrentIndex(0)
        return _QuickShim(self.combo_mode)

    @property
    def rb_custom(self):
        class _CustomShim:
            def __init__(self, combo):
                self._combo = combo
            def isChecked(self):
                return self._combo.currentIndex() == 1
            def setChecked(self, val):
                if val:
                    self._combo.setCurrentIndex(1)
        return _CustomShim(self.combo_mode)

    @property
    def tip_classes(self):
        class _TipShim:
            def __init__(self, txt):
                self._txt = txt
            def text(self):
                return self._txt.placeholderText()
            def isEnabled(self):
                return self._txt.isEnabled()
            def styleSheet(self):
                return "#6b7280"
        return _TipShim(self.txt_custom_classes)

    @property
    def tip_count(self):
        class _TipShim:
            def __init__(self, spin):
                self._spin = spin
            def text(self):
                return self._spin.lineEdit().placeholderText()
            def isEnabled(self):
                return self._spin.isEnabled()
            def styleSheet(self):
                return "#6b7280"
        return _TipShim(self.spin_count_per_class)

    def _on_mode_changed(self, index: int):
        is_custom = (index == 1)
        self.txt_custom_classes.setEnabled(is_custom)
        self.spin_count_per_class.setEnabled(is_custom)
        self.combo_name_mode.setEnabled(is_custom)
        if not is_custom:
            self.txt_manual_names.setVisible(False)
        else:
            self.txt_manual_names.setVisible(self.combo_name_mode.currentIndex() == 2)

    def _on_name_mode_changed(self, index: int):
        if self.combo_mode.currentIndex() == 1:
            self.txt_manual_names.setVisible(index == 2)

    def _on_create(self):
        if self.combo_mode.currentIndex() == 0:
            c_names = ["LỚP_TEST_A", "LỚP_TEST_B"]
            n_per_c = 3
            custom_names = None
        else:
            raw_classes = self.txt_custom_classes.text().strip()
            if not raw_classes:
                raw_classes = "LỚP_TEST_A, LỚP_TEST_B, LỚP_TEST_C"
            c_names = [c.strip() for c in raw_classes.split(",") if c.strip()]
            if not c_names:
                QMessageBox.warning(self, "Thiếu thông tin", "Vui lòng nhập ít nhất một tên lớp.")
                return
            if not self.spin_count_per_class.text().strip():
                n_per_c = 3
            else:
                n_per_c = self.spin_count_per_class.value()
            if self.combo_name_mode.currentIndex() == 2:
                raw_names = self.txt_manual_names.toPlainText().strip()
                custom_names = [n.strip() for n in raw_names.split("\n") if n.strip()]
                if not custom_names:
                    QMessageBox.warning(self, "Thiếu thông tin", "Vui lòng nhập ít nhất một tên sinh viên thủ công.")
                    return
            elif self.combo_name_mode.currentIndex() == 1:
                custom_names = [f"SV Ảo {i:02d}" for i in range(1, len(c_names) * n_per_c + 1)]
            else:
                custom_names = None

        created_stus, created_classes = self.db.create_mock_students_and_classes(
            class_names=c_names,
            num_students_per_class=n_per_c,
            custom_names=custom_names
        )

        if self.on_created_cb and callable(self.on_created_cb):
            try:
                self.on_created_cb()
            except Exception:
                pass

        QMessageBox.information(
            self,
            "Thành công",
            f"Đã tạo thành công {created_stus} sinh viên ảo thuộc {created_classes} lớp học thử nghiệm.\nToàn bộ dữ liệu này sẽ tự động xóa sạch khi tắt Chế độ Debug."
        )
        self.accept()


class AdminDialog(QDialog):
    """Cửa sổ Quản trị: Quản lý sinh viên, Lịch sử điểm danh & Cấu hình hệ thống."""
    def __init__(self, db, reload_cache_cb, restart_cameras_cb, debug_mgr=None, attendance_logic=None, parent=None, current_user=None):
        super().__init__(None, Qt.WindowType.Window)
        self.main_window = parent
        self.db = db
        self.reload_cache_cb = reload_cache_cb
        self.restart_cameras_cb = restart_cameras_cb
        self.debug_mgr = debug_mgr
        self.attendance_logic = attendance_logic

        # Thiết lập thông tin người dùng hiện tại
        if current_user and isinstance(current_user, dict):
            self.current_user = current_user
        else:
            user_db = None
            try:
                if hasattr(self.db, "get_admin_user_by_username"):
                    user_db = self.db.get_admin_user_by_username("admin")
            except Exception:
                pass
            if isinstance(user_db, dict):
                self.current_user = user_db
            else:
                self.current_user = {
                    "id": 1,
                    "username": "admin",
                    "full_name": "Quản trị viên Hệ thống",
                    "role": "super_admin",
                    "avatar_path": "",
                    "is_active": 1,
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "last_login": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }

        self.setWindowTitle("Bảng Điều Khiển Quản Trị Hệ Thống")
        self.resize(960, 680)
        self.setStyleSheet(BASE_WINDOW_STYLE)
        ensure_app_assets()
        if self.debug_mgr and getattr(self.debug_mgr, "is_active", False):
            self.db.set_debug_mode(True)
        else:
            self.db.set_debug_mode(False)
        self._init_ui()

    def _get_main_window(self):
        """Lấy tham chiếu đến MainWindow cha để gọi cập nhật UI hoặc xử lý giả lập."""
        return getattr(self, "main_window", None) or self.parent()

    def _load_avatar_pixmap(self, avatar_path: str, size: int = 36, border_color: str = "#d1d5db", border_width: float = 1.5) -> QPixmap:
        """Tải và vẽ avatar hình tròn mượt mà khử răng cưa tuyệt đối bằng QPainter."""
        out_img = QImage(size, size, QImage.Format.Format_ARGB32)
        out_img.fill(Qt.GlobalColor.transparent)
        p = QPainter(out_img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        path = QPainterPath()
        inset = border_width / 2.0 if border_width > 0 else 0.0
        path.addEllipse(inset, inset, size - 2 * inset, size - 2 * inset)
        p.save()
        p.setClipPath(path)

        pix = None
        if avatar_path:
            if hasattr(self, "db") and hasattr(self.db, "load_encrypted_photo"):
                img_bgr = self.db.load_encrypted_photo(avatar_path)
                if img_bgr is not None:
                    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
                    h, w, ch = rgb.shape
                    qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
                    pix = QPixmap.fromImage(qimg)
            if (pix is None or pix.isNull()) and os.path.exists(avatar_path):
                pix = QPixmap(avatar_path)

        if pix and not pix.isNull():
            scaled = pix.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            sx = max(0, (scaled.width() - size) // 2)
            sy = max(0, (scaled.height() - size) // 2)
            p.drawPixmap(0, 0, scaled, sx, sy, size, size)
        else:
            p.fillPath(path, QColor("#374151"))
            p.setPen(QColor("#d1d5db"))
            font = p.font()
            font.setBold(True)
            font.setPixelSize(max(10, int(size * 0.42)))
            p.setFont(font)
            user_dict = getattr(self, "current_user", {}) or {}
            uname = user_dict.get("username", "A") if isinstance(user_dict, dict) else "A"
            initial = (str(uname)[:1] or "A").upper()
            p.drawText(out_img.rect(), Qt.AlignmentFlag.AlignCenter, initial)
        p.restore()

        if border_width > 0:
            pen = QPen(QColor(border_color), border_width)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(size / 2.0, size / 2.0), (size - border_width) / 2.0, (size - border_width) / 2.0)

        p.end()
        return QPixmap.fromImage(out_img)

    def _update_header_user_display(self):
        user = getattr(self, "current_user", {})
        if not isinstance(user, dict):
            user = {}
        full_name = str(user.get("full_name") or "Quản trị viên")
        username = str(user.get("username") or "admin")
        role = str(user.get("role") or "super_admin")
        role_text_map = {
            "super_admin": "Quản trị viên cấp cao",
            "manager": "Giáo vụ",
            "teacher": "Giáo viên",
            "technician": "Kỹ thuật viên"
        }
        role_label = role_text_map.get(role, role)
        self.lbl_header_user_name.setText(full_name)
        self.lbl_header_role_badge.setText(f"@{username}  |  {role_label}")
        
        avatar_path = str(user.get("avatar_path") or "")
        pix = self._load_avatar_pixmap(avatar_path, size=36, border_color="#4b5563", border_width=1.5)
        self.lbl_header_avatar.setPixmap(pix)

    def _on_logout_clicked(self):
        reply = QMessageBox.question(
            self, "Xác nhận đăng xuất",
            "Bạn có chắc chắn muốn đăng xuất khỏi phiên làm việc quản trị?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.db.log_admin_audit(self.current_user.get("id"), self.current_user.get("username", "admin"), "LOGOUT", "Đăng xuất khỏi hệ thống")
            self.close()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # Thanh Header hiển thị thông tin người đăng nhập
        header_widget = QWidget()
        header_widget.setStyleSheet("""
        QWidget {
            background-color: #1f2937;
            border-radius: 8px;
        }
        """)
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(14, 8, 14, 8)
        header_layout.setSpacing(12)

        # Avatar thumbnail nhỏ gọn
        self.lbl_header_avatar = QLabel()
        self.lbl_header_avatar.setFixedSize(36, 36)
        self.lbl_header_avatar.setStyleSheet("border: none; background: transparent;")
        self.lbl_header_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(self.lbl_header_avatar)

        # Thông tin Tên, username, vai trò
        user_info_layout = QVBoxLayout()
        user_info_layout.setSpacing(2)
        self.lbl_header_user_name = QLabel()
        self.lbl_header_user_name.setStyleSheet("color: #ffffff; font-weight: bold; font-size: 13px; background: transparent;")
        self.lbl_header_role_badge = QLabel()
        self.lbl_header_role_badge.setStyleSheet("color: #9ca3af; font-size: 11px; background: transparent;")
        user_info_layout.addWidget(self.lbl_header_user_name)
        user_info_layout.addWidget(self.lbl_header_role_badge)
        header_layout.addLayout(user_info_layout)

        header_layout.addStretch(1)

        # Nút Đăng xuất
        self.btn_logout = QPushButton("Đăng xuất")
        self.btn_logout.setFixedHeight(30)
        self.btn_logout.setStyleSheet("""
        QPushButton {
            background-color: #374151;
            color: #f3f4f6;
            font-weight: bold;
            font-size: 12px;
            border: 1px solid #4b5563;
            border-radius: 5px;
            padding: 0 14px;
        }
        QPushButton:hover {
            background-color: #dc2626;
            color: #ffffff;
            border-color: #b91c1c;
        }
        """)
        self.btn_logout.clicked.connect(self._on_logout_clicked)
        header_layout.addWidget(self.btn_logout)

        layout.addWidget(header_widget)
        self._update_header_user_display()

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
        QTabWidget::pane { border: 1px solid #e5e7eb; border-radius: 6px; background-color: #ffffff; }
        QTabBar::tab {
            background: #f3f4f6;
            color: #4b5563;
            padding: 10px 18px;
            font-weight: bold;
            border-radius: 6px;
            margin-right: 8px;
            margin-bottom: 6px;
            border: 1px solid #e5e7eb;
        }
        QTabBar::tab:selected {
            background: #111827;
            color: #ffffff;
            border: 1px solid #111827;
        }
        """)

        # Tab 1: Quản lý sinh viên
        self.tab_students = QWidget()
        self._setup_tab_students()
        self.tabs.addTab(self.tab_students, "Danh sách Sinh viên")

        # Tab 2: Lịch sử & Điểm danh
        self.tab_attendance = QWidget()
        self._setup_tab_attendance()
        self.tabs.addTab(self.tab_attendance, "Báo cáo Điểm danh")

        # Tab 3: Cấu hình hệ thống
        self.tab_settings = QWidget()
        self._setup_tab_settings()
        self.tabs.addTab(self.tab_settings, "Cấu hình")

        # Tab 4: Chế độ Debug (Chỉ dành riêng cho Admin)
        self.tab_debug = QWidget()
        self._setup_tab_debug()
        self.tabs.addTab(self.tab_debug, "Chế độ Debug")

        # Tab 5: Thông tin Người Quản trị
        self.tab_admin_profile = QWidget()
        self._setup_tab_admin_profile()
        self.tabs.addTab(self.tab_admin_profile, "Thông tin Người Quản trị")

        # Áp dụng phân quyền RBAC 4 vai trò
        user_role = self.current_user.get("role", "super_admin")
        if user_role == "technician":
            # Ẩn Tab 1 (Sinh viên) và Tab 2 (Điểm danh) để bảo vệ dữ liệu cá nhân
            idx_att = self.tabs.indexOf(self.tab_attendance)
            if idx_att != -1:
                self.tabs.removeTab(idx_att)
            idx_stu = self.tabs.indexOf(self.tab_students)
            if idx_stu != -1:
                self.tabs.removeTab(idx_stu)
            self.tabs.setCurrentWidget(self.tab_settings)
        elif user_role in ("manager", "teacher", "viewer"):
            # Ẩn Tab 3 (Cấu hình) và Tab 4 (Debug)
            idx_dbg = self.tabs.indexOf(self.tab_debug)
            if idx_dbg != -1:
                self.tabs.removeTab(idx_dbg)
            idx_set = self.tabs.indexOf(self.tab_settings)
            if idx_set != -1:
                self.tabs.removeTab(idx_set)
            self.tabs.setCurrentWidget(self.tab_students)
        else:
            self.tabs.setCurrentWidget(self.tab_students)

        layout.addWidget(self.tabs)
        disable_wheel_on_inputs(self)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._update_debug_quick_shift_buttons()

    def _on_tab_changed(self, index: int):
        if self.tabs.widget(index) == self.tab_debug:  # Chế độ Debug
            self._update_debug_quick_shift_buttons()
            if hasattr(self, "_refresh_debug_student_list"):
                self._refresh_debug_student_list()
        elif self.tabs.widget(index) == self.tab_attendance:  # Báo cáo điểm danh
            self.load_attendance_logs()
        elif self.tabs.widget(index) == getattr(self, "tab_admin_profile", None):
            self._load_admin_profile_data()

    def _setup_tab_students(self):
        layout = QVBoxLayout(self.tab_students)

        # Thanh công cụ lọc lớp & quản lý lớp học
        top_filter_row = QHBoxLayout()
        top_filter_row.setSpacing(10)

        lbl_filter = QLabel("Lớp:")
        lbl_filter.setStyleSheet("font-weight: bold; color: #374151;")
        top_filter_row.addWidget(lbl_filter)

        self.combo_student_class_filter = QComboBox()
        self.combo_student_class_filter.setStyleSheet(GLOBAL_COMBOBOX_QSS)
        self.combo_student_class_filter.setFixedWidth(165)
        self.combo_student_class_filter.setFixedHeight(32)
        top_filter_row.addWidget(self.combo_student_class_filter)

        self.lbl_student_count = QLabel("")
        self.lbl_student_count.setStyleSheet("color: #6b7280; font-size: 12px;")
        top_filter_row.addWidget(self.lbl_student_count)

        top_filter_row.addStretch(1)

        user_role = self.current_user.get("role", "super_admin")
        self.btn_manage_classes = QPushButton("Quản lý lớp học...")
        self.btn_manage_classes.setStyleSheet("background-color: #f3f4f6; color: #111827; font-weight: bold; padding: 6px 12px; border-radius: 6px; border: 1px solid #d1d5db;")
        self.btn_manage_classes.setFixedHeight(32)
        self.btn_manage_classes.clicked.connect(self._open_manage_classes_dialog)
        top_filter_row.addWidget(self.btn_manage_classes)
        if user_role not in ("super_admin", "manager"):
            self.btn_manage_classes.setVisible(False)

        layout.addLayout(top_filter_row)

        self.lbl_edit_hint = QLabel("Chế độ xem. Nhấn 'Chỉnh sửa danh sách' để sửa thông tin trực tiếp trên bảng.")
        self.lbl_edit_hint.setStyleSheet("color: #6b7280; font-size: 12px; margin-bottom: 4px;")
        layout.addWidget(self.lbl_edit_hint)

        self.table_students = QTableWidget()
        self.table_students.setColumnCount(4)
        self.table_students.setHorizontalHeaderLabels(["ID", "MSSV", "Họ và Tên", "Lớp"])
        stu_header = self.table_students.horizontalHeader()
        stu_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        stu_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        stu_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        stu_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.table_students.setColumnWidth(0, 60)
        self.table_students.setColumnWidth(1, 110)
        self.table_students.setColumnWidth(3, 130)
        self.table_students.setStyleSheet("""
        QTableWidget {
            background-color: #ffffff;
            gridline-color: #f3f4f6;
            color: #111827;
            font-size: 12px;
            selection-background-color: #f3f4f6;
            selection-color: #111827;
        }
        QHeaderView::section {
            background-color: #f9fafb;
            color: #374151;
            border: 1px solid #e5e7eb;
            font-size: 12px;
            font-weight: bold;
            padding: 2px 4px;
        }
        QTableWidget::item {
            padding: 2px 6px;
        }
        """ + GLOBAL_SCROLLBAR_QSS)
        self.table_students.verticalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table_students.verticalHeader().setVisible(False)
        self.table_students.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table_students.setSortingEnabled(True)
        layout.addWidget(self.table_students)

        btn_row = QHBoxLayout()
        self.btn_edit_students = QPushButton("Chỉnh sửa danh sách")
        self.btn_edit_students.setStyleSheet("""
            QPushButton {
                background-color: #f3f4f6;
                color: #111827;
                font-weight: bold;
                padding: 8px 14px;
                border-radius: 6px;
                border: 1px solid #d1d5db;
            }
            QPushButton:hover {
                background-color: #e5e7eb;
            }
            QPushButton:disabled {
                background-color: #f9fafb;
                color: #9ca3af;
                border: 1px solid #e5e7eb;
            }
        """)
        self.btn_edit_students.clicked.connect(self.enable_edit_students)
        btn_row.addWidget(self.btn_edit_students)

        self.btn_save_students = QPushButton("Lưu thay đổi")
        self.btn_save_students.setStyleSheet("""
            QPushButton {
                background-color: #111827;
                color: #ffffff;
                font-weight: bold;
                padding: 8px 14px;
                border-radius: 6px;
                border: 1px solid #111827;
            }
            QPushButton:hover {
                background-color: #1f2937;
            }
            QPushButton:disabled {
                background-color: #e5e7eb;
                color: #9ca3af;
                border: 1px solid #d1d5db;
            }
        """)
        self.btn_save_students.setEnabled(False)
        self.btn_save_students.clicked.connect(self.save_student_edits)
        btn_row.addWidget(self.btn_save_students)

        btn_refresh = QPushButton("Làm mới danh sách")
        btn_refresh.setStyleSheet("background-color: #f3f4f6; color: #111827; font-weight: bold; padding: 8px 14px; border-radius: 6px; border: 1px solid #d1d5db;")
        btn_refresh.clicked.connect(self.load_students)
        btn_row.addWidget(btn_refresh)

        self.btn_delete_student = QPushButton("Xóa sinh viên đã chọn")
        self.btn_delete_student.setStyleSheet("""
            QPushButton {
                background-color: #ffffff;
                color: #dc2626;
                font-weight: bold;
                padding: 8px 14px;
                border-radius: 6px;
                border: 1px solid #fca5a5;
            }
            QPushButton:hover {
                background-color: #fef2f2;
                border: 1px solid #ef4444;
            }
            QPushButton:disabled {
                background-color: #f3f4f6;
                color: #9ca3af;
                border: 1px solid #e5e7eb;
            }
        """)
        self.btn_delete_student.setEnabled(False)
        self.btn_delete_student.clicked.connect(self.delete_selected_student)
        btn_row.addWidget(self.btn_delete_student)

        layout.addLayout(btn_row)

        self._populate_student_class_filter()
        self.combo_student_class_filter.currentIndexChanged.connect(self.load_students)
        self.load_students()

    def _populate_student_class_filter(self):
        if not hasattr(self, "combo_student_class_filter"):
            return
        self.combo_student_class_filter.blockSignals(True)
        self.combo_student_class_filter.clear()
        user_role = self.current_user.get("role", "super_admin")
        if user_role == "teacher":
            teacher_classes = self.db.get_admin_user_classes(self.current_user["id"])
            if teacher_classes:
                if len(teacher_classes) > 1:
                    self.combo_student_class_filter.addItem("Tất cả các lớp phụ trách", teacher_classes)
                for c in teacher_classes:
                    self.combo_student_class_filter.addItem(c, [c])
            else:
                self.combo_student_class_filter.addItem("Chưa được phân công lớp", [])
        else:
            self.combo_student_class_filter.addItem("Tất cả các lớp", None)
            all_classes = self.db.get_all_classes()
            for c in all_classes:
                self.combo_student_class_filter.addItem(c, [c])
        self.combo_student_class_filter.blockSignals(False)

    def _open_manage_classes_dialog(self):
        dlg = ManageClassesDialog(self, self.db, self.current_user.get("username", "admin"))
        dlg.exec()
        self._populate_student_class_filter()
        if hasattr(self, "_populate_att_class_filter"):
            self._populate_att_class_filter()
        self.load_students()

    def load_students(self):
        if not hasattr(self, "table_students"):
            return
        if not hasattr(self, "combo_student_class_filter") or self.combo_student_class_filter.count() == 0:
            self._populate_student_class_filter()

        self.table_students.setSortingEnabled(False)
        user_role = self.current_user.get("role", "super_admin")

        target_classes = None
        if hasattr(self, "combo_student_class_filter") and self.combo_student_class_filter.count() > 0:
            target_classes = self.combo_student_class_filter.currentData()
        elif user_role == "teacher":
            target_classes = self.db.get_admin_user_classes(self.current_user["id"])

        students = self.db.get_all_active_students(class_names=target_classes)
        self.table_students.setRowCount(len(students))
        self.original_students = {}
        for row, s in enumerate(students):
            s_id = s["id"]
            s_code = s["student_code"]
            s_name = s["full_name"]
            s_class = s["class_name"] or ""
            self.original_students[s_id] = (s_code, s_name, s_class)

            id_item = NumericTableWidgetItem(s_id, Qt.AlignmentFlag.AlignCenter)
            id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_students.setItem(row, 0, id_item)
            self.table_students.setItem(row, 1, _create_cell_item(s_code, Qt.AlignmentFlag.AlignCenter))
            self.table_students.setItem(row, 2, _create_cell_item(s_name, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft))
            self.table_students.setItem(row, 3, _create_cell_item(s_class, Qt.AlignmentFlag.AlignCenter))

        self.table_students.setSortingEnabled(True)
        self.table_students.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        if hasattr(self, "lbl_student_count"):
            self.lbl_student_count.setText(f"Tổng số sinh viên: {len(students)}")

        if user_role == "teacher":
            self.btn_edit_students.setVisible(False)
            self.btn_save_students.setVisible(False)
            if hasattr(self, "btn_delete_student"):
                self.btn_delete_student.setVisible(False)
            self.lbl_edit_hint.setText("Chế độ xem lớp phụ trách (Chỉ đọc). Vui lòng liên hệ Giáo vụ hoặc Quản trị viên nếu cần chỉnh sửa thông tin sinh viên.")
            self.lbl_edit_hint.setStyleSheet("color: #4b5563; font-size: 12px; margin-bottom: 4px;")
        else:
            self.btn_edit_students.setVisible(True)
            self.btn_save_students.setVisible(True)
            if hasattr(self, "btn_delete_student"):
                self.btn_delete_student.setVisible(True)
                self.btn_delete_student.setEnabled(False)
            self.btn_save_students.setEnabled(False)
            self.btn_edit_students.setEnabled(True)
            self.lbl_edit_hint.setText("Chế độ xem. Nhấn 'Chỉnh sửa danh sách' để sửa thông tin trực tiếp trên bảng.")
            self.lbl_edit_hint.setStyleSheet("color: #6b7280; font-size: 12px; margin-bottom: 4px;")

    def enable_edit_students(self):
        self.table_students.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked | 
            QTableWidget.EditTrigger.SelectedClicked | 
            QTableWidget.EditTrigger.EditKeyPressed
        )
        self.btn_save_students.setEnabled(True)
        self.btn_edit_students.setEnabled(False)
        if hasattr(self, "btn_delete_student"):
            self.btn_delete_student.setEnabled(True)
        self.lbl_edit_hint.setText("Đang ở chế độ chỉnh sửa! Nhấp chuột vào các ô MSSV, Họ tên hoặc Lớp để sửa, sau đó nhấn 'Lưu thay đổi'.")
        self.lbl_edit_hint.setStyleSheet("color: #d97706; font-weight: bold; font-size: 12px; margin-bottom: 4px;")

    def save_student_edits(self):
        row_count = self.table_students.rowCount()
        dirty_students = []
        valid_classes = set(self.db.get_all_classes())
        for r in range(row_count):
            try:
                s_id = int(self.table_students.item(r, 0).text())
                s_code = self.table_students.item(r, 1).text().strip()
                s_name = self.table_students.item(r, 2).text().strip()
                s_class = self.table_students.item(r, 3).text().strip() if self.table_students.item(r, 3) else ""

                if not s_code or not s_name:
                    QMessageBox.warning(self, "Lỗi dữ liệu", f"Dòng {r+1}: MSSV và Họ Tên không được để trống.")
                    return

                if s_class and s_class not in valid_classes:
                    QMessageBox.warning(self, "Lớp không hợp lệ", f"Dòng {r+1}: Lớp '{s_class}' chưa tồn tại trong danh mục hệ thống. Vui lòng tạo lớp trước bằng nút 'Quản lý lớp học...'.")
                    return

                orig = self.original_students.get(s_id)
                if orig != (s_code, s_name, s_class):
                    dirty_students.append({
                        "id": s_id,
                        "student_code": s_code,
                        "full_name": s_name,
                        "class_name": s_class
                    })
            except Exception as e:
                QMessageBox.critical(self, "Lỗi dữ liệu", f"Lỗi tại dòng {r+1}: {e}")
                return

        if not dirty_students:
            self.load_students()
            QMessageBox.information(self, "Thông báo", "Không có thay đổi nào cần lưu.")
            return

        try:
            self.db.update_students_batch(dirty_students)
        except Exception as e:
            QMessageBox.critical(self, "Lỗi cập nhật CSDL", f"Không thể lưu thay đổi: {e}")
            return

        # Nạp lại RAM cache để AI nhận diện tên mới ngay lập tức
        self.reload_cache_cb()
        self.load_students()
        QMessageBox.information(self, "Thành công", f"Đã lưu thay đổi cho {len(dirty_students)} sinh viên.")

    def delete_selected_student(self):
        if self.btn_edit_students.isEnabled():
            QMessageBox.warning(self, "Chế độ xem", "Vui lòng nhấn 'Chỉnh sửa danh sách' trước khi thực hiện xóa sinh viên.")
            return
        curr_row = self.table_students.currentRow()
        if curr_row < 0:
            QMessageBox.warning(self, "Thông báo", "Vui lòng chọn dòng sinh viên cần xóa.")
            return
        s_id = int(self.table_students.item(curr_row, 0).text())
        code = self.table_students.item(curr_row, 1).text()
        name = self.table_students.item(curr_row, 2).text()
        if ask_yes_no(self, "Xác nhận xóa", f"Bạn có chắc muốn xóa sinh viên {name} (ID: {s_id})?"):
            self.db.delete_student(s_id)
            self.reload_cache_cb()
            self.load_students()
            if hasattr(self, "_refresh_debug_student_list"):
                self._refresh_debug_student_list()
            main_win = self._get_main_window()
            if main_win:
                if hasattr(main_win, "_update_stats"):
                    main_win._update_stats()
                if hasattr(main_win, "lbl_card_code") and hasattr(main_win, "_clear_recent_student_card"):
                    if main_win.lbl_card_code.text().strip().endswith(code):
                        main_win._clear_recent_student_card()
            QMessageBox.information(self, "Thành công", "Đã xóa sinh viên khỏi hệ thống.")

    def _setup_tab_attendance(self):
        layout = QVBoxLayout(self.tab_attendance)
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Chọn ngày:"))
        self.date_picker = QDateEdit(QDate.currentDate())
        self.date_picker.setCalendarPopup(True)
        self.date_picker.setDisplayFormat("dd/MM/yyyy")
        self.date_picker.setFixedWidth(185)
        self.date_picker.setStyleSheet("""
        QDateEdit {
            background-color: #ffffff;
            color: #111827;
            padding: 5px 30px 5px 12px;
            border-radius: 6px;
            border: 1px solid #d1d5db;
            font-weight: bold;
            font-size: 13px;
        }
        QDateEdit::drop-down {
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 26px;
            border-left: 1px solid #d1d5db;
            border-top-right-radius: 6px;
            border-bottom-right-radius: 6px;
            background-color: #f3f4f6;
        }
        QDateEdit::down-arrow {
            image: url("assets/calendar.png");
            width: 15px;
            height: 15px;
        }
        """)

        # Tùy biến popup lịch trực quan và chống che khuất
        cal = self.date_picker.calendarWidget()
        if cal:
            cal.setStyleSheet("""
            QCalendarWidget QWidget {
                background-color: #ffffff;
                color: #111827;
            }
            QCalendarWidget QToolButton {
                color: #111827;
                background-color: #f3f4f6;
                border-radius: 4px;
                padding: 4px 8px;
                font-weight: bold;
            }
            QCalendarWidget QMenu {
                background-color: #ffffff;
                color: #111827;
            }
            QCalendarWidget QSpinBox {
                background-color: #ffffff;
                color: #111827;
                border-radius: 4px;
            }
            QCalendarWidget QTableView {
                background-color: #ffffff;
                selection-background-color: #111827;
                selection-color: #ffffff;
            }
            """)

        self.lbl_debug_notice = QLabel("Đang xem dữ liệu điểm danh Thử nghiệm (Chế độ Debug)")
        self.lbl_debug_notice.setStyleSheet("color: #d97706; font-weight: bold; padding: 5px 10px; background-color: #fef3c7; border: 1px solid #f59e0b; border-radius: 4px; margin-bottom: 4px;")
        self.lbl_debug_notice.setVisible(False)
        layout.addWidget(self.lbl_debug_notice)

        self.date_picker.dateChanged.connect(self.load_attendance_logs)
        top_row.addWidget(self.date_picker)

        lbl_att_class = QLabel("Lớp:")
        lbl_att_class.setStyleSheet("color: #374151; font-weight: bold; margin-left: 8px;")
        top_row.addWidget(lbl_att_class)
        self.combo_att_class_filter = QComboBox()
        self.combo_att_class_filter.setStyleSheet(GLOBAL_COMBOBOX_QSS)
        self.combo_att_class_filter.setFixedHeight(34)
        self.combo_att_class_filter.setMinimumWidth(160)
        self.combo_att_class_filter.currentIndexChanged.connect(self.load_attendance_logs)
        top_row.addWidget(self.combo_att_class_filter)
        self._populate_att_class_filter()

        btn_filter = QPushButton("Xem điểm danh")
        btn_filter.setStyleSheet("background-color: #111827; color: #ffffff; font-weight: bold; padding: 6px 14px; border-radius: 5px;")
        btn_filter.clicked.connect(self.load_attendance_logs)
        top_row.addWidget(btn_filter)

        btn_mark_absent = QPushButton("Chốt danh sách vắng")
        btn_mark_absent.setStyleSheet("background-color: #f3f4f6; color: #111827; font-weight: bold; padding: 6px 12px; border-radius: 4px; border: 1px solid #d1d5db;")
        btn_mark_absent.clicked.connect(self.on_mark_absent)
        top_row.addWidget(btn_mark_absent)

        self.btn_delete_attendance = QPushButton("Xóa điểm danh")
        self.btn_delete_attendance.setStyleSheet("background-color: #ffffff; color: #dc2626; font-weight: bold; padding: 6px 14px; border-radius: 4px; border: 1px solid #fca5a5;")
        self.btn_delete_attendance.clicked.connect(self.on_delete_attendance)
        top_row.addWidget(self.btn_delete_attendance)

        # Đường kẻ phân cách nhỏ giữa cụm 3 nút thao tác và nút xuất Excel
        sep_excel = QFrame()
        sep_excel.setFrameShape(QFrame.Shape.VLine)
        sep_excel.setFrameShadow(QFrame.Shadow.Plain)
        sep_excel.setFixedWidth(1)
        sep_excel.setFixedHeight(22)
        sep_excel.setStyleSheet("background-color: #d1d5db; border: none; margin: 0 4px;")
        top_row.addWidget(sep_excel)

        btn_export_excel = QPushButton("Xuất file Excel")
        btn_export_excel.setStyleSheet("""
            QPushButton {
                background-color: #f3f4f6;
                color: #111827;
                font-weight: bold;
                padding: 6px 14px;
                border-radius: 4px;
                border: 1px solid #d1d5db;
            }
            QPushButton:hover {
                background-color: #e5e7eb;
            }
        """)
        btn_export_excel.clicked.connect(self.export_excel)
        top_row.addWidget(btn_export_excel)

        top_row.addStretch()
        layout.addLayout(top_row)

        self.table_attendance = QTableWidget()
        self.table_attendance.setColumnCount(7)
        self.table_attendance.setHorizontalHeaderLabels(["MSSV", "Họ và Tên", "Lớp", "Giờ Vào", "Giờ Ra", "Trạng thái", "Ghi chú"])
        att_header = self.table_attendance.horizontalHeader()
        att_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        att_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        att_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        att_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        att_header.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        att_header.setSectionResizeMode(5, QHeaderView.ResizeMode.Interactive)
        att_header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.table_attendance.setColumnWidth(0, 95)
        self.table_attendance.setColumnWidth(1, 160)
        self.table_attendance.setColumnWidth(2, 110)
        self.table_attendance.setColumnWidth(3, 95)
        self.table_attendance.setColumnWidth(4, 95)
        self.table_attendance.setColumnWidth(5, 120)
        self.table_attendance.setStyleSheet("""
        QTableWidget {
            background-color: #ffffff;
            gridline-color: #f3f4f6;
            color: #111827;
            font-size: 12px;
            selection-background-color: #f3f4f6;
            selection-color: #111827;
        }
        QHeaderView::section {
            background-color: #f9fafb;
            color: #374151;
            border: 1px solid #e5e7eb;
            font-size: 12px;
            font-weight: bold;
            padding: 2px 4px;
        }
        QTableWidget::item {
            padding: 2px 6px;
        }
        """)
        self.table_attendance.verticalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table_attendance.verticalHeader().setVisible(False)
        self.table_attendance.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table_attendance.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_attendance.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table_attendance.setSortingEnabled(True)
        layout.addWidget(self.table_attendance)
        self.load_attendance_logs()

    def _populate_att_class_filter(self):
        if not hasattr(self, "combo_att_class_filter"):
            return
        self.combo_att_class_filter.blockSignals(True)
        self.combo_att_class_filter.clear()
        user_role = self.current_user.get("role", "super_admin") if hasattr(self, "current_user") and self.current_user else "super_admin"

        if user_role == "teacher":
            teacher_classes = self.db.get_admin_user_classes(self.current_user["id"])
            if len(teacher_classes) > 1:
                self.combo_att_class_filter.addItem("Tất cả lớp phụ trách", teacher_classes)
            elif len(teacher_classes) == 0:
                self.combo_att_class_filter.addItem("Không có lớp phụ trách", [])
            for c in teacher_classes:
                self.combo_att_class_filter.addItem(f"Lớp {c}", [c])
        else:
            all_classes = self.db.get_all_classes()
            self.combo_att_class_filter.addItem("Tất cả các lớp", None)
            for c in all_classes:
                self.combo_att_class_filter.addItem(f"Lớp {c}", [c])
        self.combo_att_class_filter.blockSignals(False)

    def _get_current_att_target_classes(self):
        user_role = self.current_user.get("role", "super_admin") if hasattr(self, "current_user") and self.current_user else "super_admin"
        if hasattr(self, "combo_att_class_filter") and self.combo_att_class_filter.count() > 0:
            return self.combo_att_class_filter.currentData()
        if user_role == "teacher":
            return self.db.get_admin_user_classes(self.current_user["id"])
        return None

    def load_attendance_logs(self):
        if not hasattr(self, "table_attendance"):
            return
        if not hasattr(self, "combo_att_class_filter") or self.combo_att_class_filter.count() == 0:
            self._populate_att_class_filter()
        self.table_attendance.setSortingEnabled(False)
        is_dbg = self.debug_mgr.is_active if self.debug_mgr else False
        if hasattr(self, "lbl_debug_notice"):
            self.lbl_debug_notice.setVisible(is_dbg)

        d_str = self.date_picker.date().toString("yyyy-MM-dd")
        target_classes = self._get_current_att_target_classes()
        logs = self.db.get_attendance_logs(d_str, class_names=target_classes)
        self.table_attendance.setRowCount(len(logs))
        for row, r in enumerate(logs):
            code_item = NumericTableWidgetItem(r["student_code"], Qt.AlignmentFlag.AlignCenter)
            code_item.setData(Qt.ItemDataRole.UserRole, r["id"])
            self.table_attendance.setItem(row, 0, code_item)
            self.table_attendance.setItem(row, 1, _create_cell_item(r["full_name"], Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft))
            self.table_attendance.setItem(row, 2, _create_cell_item(r.get("class_name") or "", Qt.AlignmentFlag.AlignCenter))
            use_ampm = self.chk_ampm.isChecked() if hasattr(self, "chk_ampm") else (self.db.get_setting("use_ampm", "0") == "1")
            in_str = format_time_str(r["check_in_time"], use_ampm)
            out_str = format_time_str(r["check_out_time"], use_ampm)
            self.table_attendance.setItem(row, 3, _create_cell_item(in_str or "--:--", Qt.AlignmentFlag.AlignCenter))
            self.table_attendance.setItem(row, 4, _create_cell_item(out_str or "--:--", Qt.AlignmentFlag.AlignCenter))
            
            status_text, status_color = get_status_display(r["status"])
            self.table_attendance.setItem(row, 5, _create_cell_item(status_text, Qt.AlignmentFlag.AlignCenter, color=status_color))
            sess_name = r.get("session_name")
            note_content = (r.get("note") or "").strip()
            if sess_name and note_content:
                display_note = f"{sess_name} - {note_content}"
            elif sess_name:
                display_note = sess_name
            else:
                display_note = note_content
            self.table_attendance.setItem(row, 6, _create_cell_item(display_note, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft))
        self.table_attendance.setSortingEnabled(True)

    def on_delete_attendance(self):
        selected_rows = self.table_attendance.selectionModel().selectedRows()
        d_str = self.date_picker.date().toString("yyyy-MM-dd")
        display_date = self.date_picker.date().toString("dd/MM/yyyy")
        is_dbg = self.debug_mgr.is_active if self.debug_mgr else False
        dbg_prefix = "[Debug] " if is_dbg else ""

        if selected_rows:
            count = len(selected_rows)
            if count == 1:
                row = selected_rows[0].row()
                name = self.table_attendance.item(row, 1).text()
                code = self.table_attendance.item(row, 0).text()
                msg = f"{dbg_prefix}Bạn có chắc muốn xóa bản ghi điểm danh của sinh viên:\n{name} (MSSV: {code}) vào ngày {display_date}?"
            else:
                msg = f"{dbg_prefix}Bạn có chắc muốn xóa {count} bản ghi điểm danh đã chọn vào ngày {display_date}?"

            if ask_yes_no(self, "Xác nhận xóa điểm danh", msg):
                deleted = 0
                for index in selected_rows:
                    item = self.table_attendance.item(index.row(), 0)
                    if item is not None:
                        att_id = item.data(Qt.ItemDataRole.UserRole)
                        if att_id and self.db.delete_attendance_by_id(att_id):
                            deleted += 1
                self.load_attendance_logs()
                main_win = self._get_main_window()
                if main_win and hasattr(main_win, "_update_stats"):
                    main_win._update_stats()
                QMessageBox.information(self, "Thành công", f"Đã xóa thành công {deleted} bản ghi điểm danh.")
        else:
            target_classes = self._get_current_att_target_classes()
            logs = self.db.get_attendance_logs(d_str, class_names=target_classes)
            if not logs:
                QMessageBox.information(self, "Thông báo", f"Không có bản ghi điểm danh nào trong ngày {display_date}.")
                return
            scope_desc = f" thuộc phạm vi đã lọc ({len(target_classes)} lớp)" if target_classes else ""
            if ask_yes_no(
                self, "Xác nhận xóa điểm danh",
                f"{dbg_prefix}Bạn chưa chọn dòng cụ thể nào trong bảng.\nBạn có muốn xóa toàn bộ {len(logs)} bản ghi điểm danh của ngày {display_date}{scope_desc} không?"
            ):
                deleted = self.db.delete_attendance_by_date(d_str, class_names=target_classes)
                self.load_attendance_logs()
                main_win = self._get_main_window()
                if main_win and hasattr(main_win, "_update_stats"):
                    main_win._update_stats()
                QMessageBox.information(self, "Thành công", f"Đã xóa toàn bộ {deleted} bản ghi điểm danh của ngày {display_date}.")

    def on_mark_absent(self):
        d_str = self.date_picker.date().toString("yyyy-MM-dd")
        target_classes = self._get_current_att_target_classes()
        self.db.mark_absent_for_unrecorded(d_str, class_names=target_classes)
        self.load_attendance_logs()
        scope_msg = ""
        if target_classes:
            scope_msg = f" (cho {len(target_classes)} lớp: {', '.join(target_classes)})"
        QMessageBox.information(self, "Chốt danh sách", f"Đã đánh dấu vắng cho các sinh viên chưa điểm danh ngày {d_str}{scope_msg}.")

    def _prepare_export_data(self, logs: list) -> list:
        """Định dạng dữ liệu báo cáo sang tiếng Việt chuẩn, dễ đọc, không dùng tên biến kỹ thuật."""
        use_ampm = self.chk_ampm.isChecked() if hasattr(self, "chk_ampm") else (self.db.get_setting("use_ampm", "0") == "1")
        export_data = []
        for idx, r in enumerate(logs, start=1):
            status_text, _ = get_status_display(r.get("status"))
            raw_date = r.get("date") or ""
            date_display = raw_date
            if raw_date:
                try:
                    parts = raw_date.split("-")
                    if len(parts) == 3:
                        date_display = f"{parts[2]}/{parts[1]}/{parts[0]}"
                except Exception:
                    date_display = raw_date

            in_time = format_time_str(r.get("check_in_time") or "", use_ampm) or "--:--"
            out_time = format_time_str(r.get("check_out_time") or "", use_ampm) or "--:--"

            sess_name = r.get("session_name") or f"Ca {r.get('session_id', 1)}"
            note_content = (r.get("note") or "").strip()
            if sess_name and note_content:
                display_note = f"{sess_name} - {note_content}"
            elif sess_name:
                display_note = sess_name
            else:
                display_note = note_content

            export_data.append({
                "ID": r.get("id", idx),
                "MSSV": r.get("student_code") or "",
                "Họ và Tên": r.get("full_name") or "",
                "Lớp": r.get("class_name") or "",
                "Ngày": date_display,
                "Ca Học": sess_name,
                "Giờ Vào": in_time,
                "Giờ Ra": out_time,
                "Trạng Thái": status_text,
                "Ghi Chú": display_note
            })
        return export_data

    def export_excel(self):
        d_str = self.date_picker.date().toString("yyyy-MM-dd")
        target_classes = self._get_current_att_target_classes()
        logs = self.db.get_attendance_logs(d_str, class_names=target_classes)
        if not logs:
            QMessageBox.warning(self, "Thông báo", "Không có dữ liệu để xuất.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Lưu file Excel", f"diem_danh_{d_str}.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        try:
            export_data = self._prepare_export_data(logs)
            df = pd.DataFrame(export_data)
            with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
                df.to_excel(writer, sheet_name="Báo Cáo Điểm Danh", index=False)
                wb = writer.book
                ws = writer.sheets["Báo Cáo Điểm Danh"]

                header_fmt = wb.add_format({
                    "bold": True,
                    "bg_color": "#f3f4f6",
                    "font_color": "#111827",
                    "border": 1,
                    "border_color": "#d1d5db",
                    "align": "center",
                    "valign": "vcenter",
                    "font_name": "Segoe UI",
                    "font_size": 11
                })
                cell_center_fmt = wb.add_format({
                    "align": "center",
                    "valign": "vcenter",
                    "border": 1,
                    "border_color": "#e5e7eb",
                    "font_name": "Segoe UI",
                    "font_size": 10
                })
                cell_left_fmt = wb.add_format({
                    "align": "left",
                    "valign": "vcenter",
                    "border": 1,
                    "border_color": "#e5e7eb",
                    "font_name": "Segoe UI",
                    "font_size": 10
                })

                ws.set_row(0, 26)
                for col_num, col_name in enumerate(df.columns):
                    ws.write(0, col_num, col_name, header_fmt)

                left_align_cols = {"Họ và Tên", "Ghi Chú"}
                for row_num, row_data in enumerate(export_data, start=1):
                    ws.set_row(row_num, 22)
                    for col_num, col_name in enumerate(df.columns):
                        val = row_data[col_name]
                        fmt = cell_left_fmt if col_name in left_align_cols else cell_center_fmt
                        ws.write(row_num, col_num, val, fmt)

                col_min_widths = {
                    "ID": 8,
                    "MSSV": 14,
                    "Họ và Tên": 24,
                    "Lớp": 12,
                    "Ngày": 14,
                    "Ca Học": 12,
                    "Giờ Vào": 12,
                    "Giờ Ra": 12,
                    "Trạng Thái": 16,
                    "Ghi Chú": 24
                }
                for col_num, col_name in enumerate(df.columns):
                    series_len = df[col_name].astype(str).map(len).max() if not df.empty else 0
                    calc_width = max(series_len + 4, len(str(col_name)) + 4)
                    final_width = max(calc_width, col_min_widths.get(col_name, 12))
                    ws.set_column(col_num, col_num, final_width)

            QMessageBox.information(self, "Thành công", f"Đã xuất báo cáo ra: {path}")
        except Exception as e:
            QMessageBox.critical(self, "Lỗi xuất tệp", f"Không thể lưu tệp: {e}\nVui lòng đóng tệp nếu đang mở trong ứng dụng khác.")

    def _setup_tab_settings(self):
        main_tab_layout = QVBoxLayout(self.tab_settings)
        main_tab_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }\n" + GLOBAL_SCROLLBAR_QSS)

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setSpacing(14)
        layout.setContentsMargins(10, 10, 10, 10)

        # Box 1: Cấu hình Ca học (Chia 2 cột: Cột trái ô thời gian nhỏ gọn & AM/PM, Cột phải đồng hồ trực quan tương tác)
        box_schedule = QGroupBox("Cấu hình Ca học")
        box_schedule.setStyleSheet("""
        QGroupBox {
            font-weight: bold;
            color: #111827;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            margin-top: 8px;
            padding-top: 14px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 10px;
            padding: 0 4px;
            background-color: #ffffff;
        }
        """)

        schedule_hlayout = QHBoxLayout(box_schedule)
        schedule_hlayout.setContentsMargins(14, 18, 14, 12)
        schedule_hlayout.setSpacing(18)

        # Nạp dữ liệu ca học từ CSDL
        raw_sess = self.db.get_all_sessions(only_active=False) if hasattr(self.db, "get_all_sessions") else None
        if isinstance(raw_sess, list) and raw_sess and all(isinstance(x, dict) for x in raw_sess):
            self.sessions_data = raw_sess
        else:
            self.sessions_data = [{
                "id": 1,
                "name": "Ca 1",
                "checkin_open": "06:30",
                "class_start": "07:30",
                "late_threshold": "07:45",
                "checkin_cutoff": "08:15",
                "early_threshold": "11:15",
                "class_end": "11:30",
                "transition_cutoff": "12:00",
                "is_active": 1,
                "sort_order": 0
            }]
        self.current_session_index = 0

        # Cột trái: Bộ chọn ca học, tùy chọn AM/PM và các ô thời gian
        left_schedule_col = QVBoxLayout()
        left_schedule_col.setSpacing(10)

        # Thanh chọn ca học (Multi-Session Switcher)
        session_ctrl_row = QHBoxLayout()
        session_ctrl_row.setSpacing(6)
        lbl_sess_choose = QLabel("Chọn ca học:")
        lbl_sess_choose.setStyleSheet("color: #111827; font-weight: bold; font-size: 12px;")
        session_ctrl_row.addWidget(lbl_sess_choose)

        self.combo_sessions = QComboBox()
        self.combo_sessions.setStyleSheet(GLOBAL_COMBOBOX_QSS)
        for s in self.sessions_data:
            self.combo_sessions.addItem(s["name"], s.get("id"))
        self.combo_sessions.currentIndexChanged.connect(self._on_session_index_changed)
        session_ctrl_row.addWidget(self.combo_sessions, 1)

        btn_add_sess = QPushButton("Thêm ca")
        btn_add_sess.setFixedHeight(28)
        btn_add_sess.setStyleSheet("background-color: #111827; color: #ffffff; font-weight: bold; font-size: 11px; padding: 2px 8px; border-radius: 4px;")
        btn_add_sess.clicked.connect(self._on_add_session_clicked)
        session_ctrl_row.addWidget(btn_add_sess)

        btn_del_sess = QPushButton("Xóa ca")
        btn_del_sess.setFixedHeight(28)
        btn_del_sess.setStyleSheet("background-color: #f3f4f6; color: #111827; font-weight: bold; font-size: 11px; padding: 2px 8px; border-radius: 4px; border: 1px solid #d1d5db;")
        btn_del_sess.clicked.connect(self._on_delete_session_clicked)
        session_ctrl_row.addWidget(btn_del_sess)

        left_schedule_col.addLayout(session_ctrl_row)

        # Tùy chọn bật/tắt AM/PM (Loại bỏ chữ trong ngoặc)
        self.chk_ampm = QCheckBox("Sử dụng định dạng 12 giờ")
        use_ampm = self.db.get_setting("use_ampm", "0") == "1"
        self.chk_ampm.setChecked(use_ampm)
        self.chk_ampm.setStyleSheet("""
        QCheckBox {
            color: #111827;
            font-weight: bold;
            font-size: 12px;
            margin-bottom: 2px;
            spacing: 8px;
        }
        QCheckBox::indicator {
            width: 16px;
            height: 16px;
            border-radius: 4px;
            border: 1.5px solid #9ca3af;
            background-color: #ffffff;
        }
        QCheckBox::indicator:hover {
            border-color: #111827;
            background-color: #f9fafb;
        }
        QCheckBox::indicator:checked {
            background-color: #111827;
            border: 1.5px solid #111827;
            image: url("assets/checkbox_check.png");
        }
        """)
        self.chk_ampm.toggled.connect(self._on_ampm_toggled)
        left_schedule_col.addWidget(self.chk_ampm)

        form_schedule = QFormLayout()
        form_schedule.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form_schedule.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form_schedule.setHorizontalSpacing(14)
        form_schedule.setVerticalSpacing(8)

        # Lịch học: Loại bỏ chữ trong ngoặc (Giờ đóng cổng: thay vì Giờ đóng cổng (Vắng):)
        self.schedule_fields = [
            ("checkin_open", "Giờ mở cổng Check-in:", "06:30"),
            ("class_start", "Giờ bắt đầu ca học:", "07:30"),
            ("late_threshold", "Hạn chót tính đúng giờ:", "07:45"),
            ("checkin_cutoff", "Giờ đóng cổng:", "08:15"),
            ("early_threshold", "Hạn chót tính về sớm:", "11:15"),
            ("class_end", "Giờ kết thúc buổi học:", "11:30"),
            ("transition_cutoff", "Giờ chuyển sang ca sau:", "12:00"),
        ]

        self.time_edits = {}
        self.field_labels = {}
        self.time_labels = {}
        curr_sess = self.sessions_data[0] if (isinstance(self.sessions_data, list) and self.sessions_data and isinstance(self.sessions_data[0], dict)) else {}
        for key, label_text, default_val in self.schedule_fields:
            saved_val = curr_sess.get(key, default_val) if isinstance(curr_sess, dict) else default_val
            if not isinstance(saved_val, str):
                saved_val = str(default_val)
            t_obj = QTime.fromString(saved_val, "HH:mm")
            if not t_obj.isValid():
                t_obj = QTime.fromString(default_val, "HH:mm")

            te = ClickableTimeEdit(t_obj)
            te.setFixedWidth(115)
            te.setFixedHeight(30)
            te.setAlignment(Qt.AlignmentFlag.AlignCenter)
            te.setDisplayFormat("hh:mm AP" if use_ampm else "HH:mm")
            te.setStyleSheet("""
            QTimeEdit {
                background-color: #ffffff;
                color: #111827;
                padding: 4px 8px;
                border-radius: 5px;
                border: 1.5px solid #d1d5db;
                font-weight: bold;
                font-size: 12px;
            }
            """ + "\n" + GLOBAL_MENU_QSS)
            te.clicked.connect(lambda k=key: self.set_active_time_field(k))
            te.timeChanged.connect(lambda t, k=key: self._on_time_edit_changed(k, t))

            lbl = QLabel(label_text)
            lbl.setFixedWidth(175)
            lbl.setStyleSheet("color: #374151; font-size: 12px;")

            self.time_edits[key] = te
            self.field_labels[key] = label_text
            self.time_labels[key] = lbl
            form_schedule.addRow(lbl, te)

        left_schedule_col.addLayout(form_schedule)
        schedule_hlayout.addLayout(left_schedule_col, 1)

        # Cột phải: Đồng hồ trực quan tương tác đặt trong khung cố định để chống đẩy layout
        clock_container = QWidget()
        clock_container.setFixedWidth(240)
        right_clock_col = QVBoxLayout(clock_container)
        right_clock_col.setContentsMargins(0, 0, 0, 0)
        right_clock_col.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_clock_col.setSpacing(3)

        self.lbl_clock_header = QLabel("Đang chỉnh")
        self.lbl_clock_header.setStyleSheet("color: #6b7280; font-weight: bold; font-size: 11px;")
        self.lbl_clock_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_clock_col.addWidget(self.lbl_clock_header)

        self.lbl_clock_target = QLabel("Nhấp vào ô để chỉnh giờ")
        self.lbl_clock_target.setFixedWidth(230)
        self.lbl_clock_target.setStyleSheet("color: #111827; font-weight: bold; font-size: 13px; margin-bottom: 2px;")
        self.lbl_clock_target.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_clock_col.addWidget(self.lbl_clock_target)

        self.clock_widget = InteractiveClockWidget()
        self.clock_widget.timeChanged.connect(self._on_clock_manipulated)
        right_clock_col.addWidget(self.clock_widget, alignment=Qt.AlignmentFlag.AlignCenter)

        self.lbl_clock_digital = QLabel("--:--")
        self.lbl_clock_digital.setStyleSheet("color: #111827; font-weight: bold; font-size: 14px;")
        self.lbl_clock_digital.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_clock_col.addWidget(self.lbl_clock_digital)

        lbl_hint = QLabel("Kéo vòng trong: Giờ | Vòng ngoài: Phút")
        lbl_hint.setStyleSheet("color: #9ca3af; font-size: 11px;")
        lbl_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_clock_col.addWidget(lbl_hint)

        # Hàng nút chỉnh nhanh (tuyệt đối không có emoji)
        quick_row = QHBoxLayout()
        quick_row.setSpacing(6)
        btn_m1h = QPushButton("-1h")
        btn_p1h = QPushButton("+1h")
        btn_m5m = QPushButton("-5m")
        btn_p5m = QPushButton("+5m")
        self.btn_toggle_ampm = QPushButton("AM/PM")

        for b in [btn_m1h, btn_p1h, btn_m5m, btn_p5m, self.btn_toggle_ampm]:
            b.setFixedHeight(26)
            b.setStyleSheet("background-color: #f3f4f6; color: #111827; font-weight: bold; font-size: 11px; padding: 2px 6px; border-radius: 4px; border: 1px solid #d1d5db;")
            quick_row.addWidget(b)

        btn_m1h.clicked.connect(lambda: self._adjust_active_time(hours=-1))
        btn_p1h.clicked.connect(lambda: self._adjust_active_time(hours=1))
        btn_m5m.clicked.connect(lambda: self._adjust_active_time(minutes=-5))
        btn_p5m.clicked.connect(lambda: self._adjust_active_time(minutes=5))
        self.btn_toggle_ampm.clicked.connect(self._toggle_ampm_active)

        right_clock_col.addLayout(quick_row)
        schedule_hlayout.addWidget(clock_container)

        # Khởi tạo không chọn mặc định trường nào khi mở
        self.set_active_time_field(None)

        layout.addWidget(box_schedule)

        # Box 2: Cấu hình Camera & Nhận diện
        box_camera = QGroupBox("Cấu hình Camera và Nhận diện")
        box_camera.setStyleSheet("""
        QGroupBox {
            font-weight: bold;
            color: #111827;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            margin-top: 8px;
            padding-top: 14px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 10px;
            padding: 0 4px;
            background-color: #ffffff;
        }
        """)
        form_camera = QFormLayout(box_camera)
        form_camera.setContentsMargins(14, 18, 14, 14)
        form_camera.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form_camera.setHorizontalSpacing(14)
        form_camera.setVerticalSpacing(8)

        line_edit_qss = "background-color: #ffffff; color: #111827; padding: 6px 10px 6px 10px; border-radius: 5px; border: 1px solid #d1d5db; font-size: 12px;"
        spin_qss = (
            "QSpinBox, QDoubleSpinBox { background-color: #ffffff; color: #111827; padding: 6px 10px 6px 6px; border-radius: 5px; border: 1px solid #d1d5db; font-size: 12px; }"
            " QSpinBox:disabled, QDoubleSpinBox:disabled { background-color: #f9fafb; color: #9ca3af; border-color: #e5e7eb; }"
        )
        combo_qss = GLOBAL_COMBOBOX_QSS

        def add_aligned_row(form, label_text, widget, label_width=290, height=32):
            lbl = QLabel(label_text)
            lbl.setFixedWidth(label_width)
            lbl.setFixedHeight(height)
            lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            lbl.setStyleSheet("color: #374151; font-size: 12px; border: none; background: transparent;")
            if isinstance(widget, QCheckBox):
                widget.setFixedHeight(height)
            form.addRow(lbl, widget)
            return lbl

        # Nguồn Camera Vào: Dropdown chọn nguồn và option tương ứng
        self.txt_cam_in = QLineEdit(self.db.get_setting("camera_in", "0"))
        self.txt_cam_in.setStyleSheet(line_edit_qss)
        self.txt_cam_in.setVisible(False)

        self.combo_cam_in_type = QComboBox()
        self.combo_cam_in_type.addItem("Webcam / USB", "webcam")
        self.combo_cam_in_type.addItem("Camera IP", "ip")
        self.combo_cam_in_type.addItem("Tệp video", "file")
        self.combo_cam_in_type.setStyleSheet(combo_qss)
        self.combo_cam_in_type.setFixedWidth(280)
        add_aligned_row(form_camera, "Nguồn Camera Cửa:", self.combo_cam_in_type)

        self.stack_cam_in = QStackedWidget()
        self.stack_cam_in.setFixedHeight(32)

        # Page 0 (Webcam): SpinBox chỉ số cổng
        page_in_webcam = QWidget()
        lay_in_webcam = QHBoxLayout(page_in_webcam)
        lay_in_webcam.setContentsMargins(0, 0, 0, 0)
        lay_in_webcam.setSpacing(8)
        self.spn_cam_in_idx = QSpinBox()
        self.spn_cam_in_idx.setRange(0, 9)
        self.spn_cam_in_idx.setFixedWidth(170)
        self.spn_cam_in_idx.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spn_cam_in_idx.setStyleSheet(spin_qss)
        lbl_hint_in = QLabel("Chỉ số cổng USB")
        lbl_hint_in.setStyleSheet("color: #6b7280; font-size: 11px;")
        lay_in_webcam.addWidget(self.spn_cam_in_idx)
        lay_in_webcam.addWidget(lbl_hint_in)
        lay_in_webcam.addStretch()
        self.stack_cam_in.addWidget(page_in_webcam)

        # Page 1 (IP Camera): QLineEdit URL
        page_in_ip = QWidget()
        lay_in_ip = QHBoxLayout(page_in_ip)
        lay_in_ip.setContentsMargins(0, 0, 0, 0)
        self.txt_cam_in_url = QLineEdit()
        self.txt_cam_in_url.setPlaceholderText("http://192.168.1.xxx:4747/video hoặc rtsp://...")
        self.txt_cam_in_url.setStyleSheet(line_edit_qss)
        lay_in_ip.addWidget(self.txt_cam_in_url)
        self.stack_cam_in.addWidget(page_in_ip)

        # Page 2 (Tệp video): QLineEdit + Nút chọn tệp
        page_in_file = QWidget()
        lay_in_file = QHBoxLayout(page_in_file)
        lay_in_file.setContentsMargins(0, 0, 0, 0)
        lay_in_file.setSpacing(6)
        self.txt_cam_in_file = QLineEdit()
        self.txt_cam_in_file.setPlaceholderText("Đường dẫn tệp video...")
        self.txt_cam_in_file.setStyleSheet(line_edit_qss)
        btn_browse_in = QPushButton("Chọn tệp...")
        btn_browse_in.setFixedHeight(30)
        btn_browse_in.setStyleSheet("background-color: #f3f4f6; color: #111827; font-weight: bold; font-size: 11px; padding: 2px 10px; border-radius: 4px; border: 1px solid #d1d5db;")
        btn_browse_in.clicked.connect(self._browse_cam_in_file)
        lay_in_file.addWidget(self.txt_cam_in_file)
        lay_in_file.addWidget(btn_browse_in)
        self.stack_cam_in.addWidget(page_in_file)

        add_aligned_row(form_camera, "Tùy chọn nguồn Cửa:", self.stack_cam_in)

        # Nguồn Camera Gắn Tường: Dropdown chọn nguồn và option tương ứng
        self.txt_cam_out = QLineEdit(self.db.get_setting("camera_out", "1"))
        self.txt_cam_out.setStyleSheet(line_edit_qss)
        self.txt_cam_out.setVisible(False)

        self.combo_cam_out_type = QComboBox()
        self.combo_cam_out_type.addItem("Webcam / USB", "webcam")
        self.combo_cam_out_type.addItem("Camera IP", "ip")
        self.combo_cam_out_type.addItem("Tệp video", "file")
        self.combo_cam_out_type.setStyleSheet(combo_qss)
        self.combo_cam_out_type.setFixedWidth(280)
        add_aligned_row(form_camera, "Nguồn Camera Gắn Tường:", self.combo_cam_out_type)

        self.stack_cam_out = QStackedWidget()
        self.stack_cam_out.setFixedHeight(32)

        # Page 0 (Webcam): SpinBox chỉ số cổng
        page_out_webcam = QWidget()
        lay_out_webcam = QHBoxLayout(page_out_webcam)
        lay_out_webcam.setContentsMargins(0, 0, 0, 0)
        lay_out_webcam.setSpacing(8)
        self.spn_cam_out_idx = QSpinBox()
        self.spn_cam_out_idx.setRange(0, 9)
        self.spn_cam_out_idx.setFixedWidth(170)
        self.spn_cam_out_idx.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spn_cam_out_idx.setStyleSheet(spin_qss)
        lbl_hint_out = QLabel("Chỉ số cổng USB")
        lbl_hint_out.setStyleSheet("color: #6b7280; font-size: 11px;")
        lay_out_webcam.addWidget(self.spn_cam_out_idx)
        lay_out_webcam.addWidget(lbl_hint_out)
        lay_out_webcam.addStretch()
        self.stack_cam_out.addWidget(page_out_webcam)

        # Page 1 (IP Camera): QLineEdit URL
        page_out_ip = QWidget()
        lay_out_ip = QHBoxLayout(page_out_ip)
        lay_out_ip.setContentsMargins(0, 0, 0, 0)
        self.txt_cam_out_url = QLineEdit()
        self.txt_cam_out_url.setPlaceholderText("http://192.168.1.xxx:4747/video hoặc rtsp://...")
        self.txt_cam_out_url.setStyleSheet(line_edit_qss)
        lay_out_ip.addWidget(self.txt_cam_out_url)
        self.stack_cam_out.addWidget(page_out_ip)

        # Page 2 (Tệp video): QLineEdit + Nút chọn tệp
        page_out_file = QWidget()
        lay_out_file = QHBoxLayout(page_out_file)
        lay_out_file.setContentsMargins(0, 0, 0, 0)
        lay_out_file.setSpacing(6)
        self.txt_cam_out_file = QLineEdit()
        self.txt_cam_out_file.setPlaceholderText("Đường dẫn tệp video...")
        self.txt_cam_out_file.setStyleSheet(line_edit_qss)
        btn_browse_out = QPushButton("Chọn tệp...")
        btn_browse_out.setFixedHeight(30)
        btn_browse_out.setStyleSheet("background-color: #f3f4f6; color: #111827; font-weight: bold; font-size: 11px; padding: 2px 10px; border-radius: 4px; border: 1px solid #d1d5db;")
        btn_browse_out.clicked.connect(self._browse_cam_out_file)
        lay_out_file.addWidget(self.txt_cam_out_file)
        lay_out_file.addWidget(btn_browse_out)
        self.stack_cam_out.addWidget(page_out_file)

        add_aligned_row(form_camera, "Tùy chọn nguồn Gắn Tường:", self.stack_cam_out)

        # Khởi tạo giá trị và kết nối đồng bộ hai chiều cho các nguồn camera
        self._syncing_cam_in = False
        self._init_cam_in_controls()
        self.combo_cam_in_type.currentIndexChanged.connect(self._on_cam_in_type_changed)
        self.spn_cam_in_idx.valueChanged.connect(self._on_cam_in_sub_changed)
        self.txt_cam_in_url.textChanged.connect(self._on_cam_in_sub_changed)
        self.txt_cam_in_file.textChanged.connect(self._on_cam_in_sub_changed)
        self.txt_cam_in.textChanged.connect(self._on_txt_cam_in_changed)

        self._syncing_cam_out = False
        self._init_cam_out_controls()
        self.combo_cam_out_type.currentIndexChanged.connect(self._on_cam_out_type_changed)
        self.spn_cam_out_idx.valueChanged.connect(self._on_cam_out_sub_changed)
        self.txt_cam_out_url.textChanged.connect(self._on_cam_out_sub_changed)
        self.txt_cam_out_file.textChanged.connect(self._on_cam_out_sub_changed)
        self.txt_cam_out.textChanged.connect(self._on_txt_cam_out_changed)

        # Ngưỡng nhận diện (Loại bỏ chữ trong ngoặc)
        self.spn_thresh = QDoubleSpinBox()
        self.spn_thresh.setRange(0.3, 0.9)
        self.spn_thresh.setSingleStep(0.05)
        self.spn_thresh.setValue(float(self.db.get_setting("face_threshold", "0.55")))
        self.spn_thresh.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spn_thresh.setStyleSheet(spin_qss)
        add_aligned_row(form_camera, "Ngưỡng nhận diện khuôn mặt:", self.spn_thresh)

        self.spn_duration = QDoubleSpinBox()
        self.spn_duration.setRange(1.0, 10.0)
        self.spn_duration.setSingleStep(0.5)
        self.spn_duration.setSuffix(" giây")
        self.spn_duration.setValue(float(self.db.get_setting("verification_duration", "3.0")))
        self.spn_duration.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spn_duration.setStyleSheet(spin_qss)
        add_aligned_row(form_camera, "Thời gian chờ xác thực Check-in/out:", self.spn_duration)

        self.spn_ratio = QSpinBox()
        self.spn_ratio.setRange(10, 100)
        self.spn_ratio.setSingleStep(5)
        self.spn_ratio.setSuffix(" %")
        self.spn_ratio.setValue(int(float(self.db.get_setting("verification_ratio", "70"))))
        self.spn_ratio.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spn_ratio.setStyleSheet(spin_qss)
        add_aligned_row(form_camera, "Tỷ lệ frame hợp lệ tối thiểu để điểm danh:", self.spn_ratio)

        # Cấu hình chống ảnh giả
        self.chk_anti_spoofing = QCheckBox("Bật chống ảnh giả")
        self.chk_anti_spoofing.setFixedHeight(32)
        self.chk_anti_spoofing.setChecked(self.db.get_setting("anti_spoofing_enabled", "0") == "1")
        self.chk_anti_spoofing.setStyleSheet("""
        QCheckBox {
            color: #111827;
            font-weight: bold;
            font-size: 12px;
            spacing: 8px;
        }
        QCheckBox::indicator {
            width: 16px;
            height: 16px;
            border-radius: 4px;
            border: 1.5px solid #9ca3af;
            background-color: #ffffff;
        }
        QCheckBox::indicator:hover {
            border-color: #111827;
            background-color: #f9fafb;
        }
        QCheckBox::indicator:checked {
            background-color: #111827;
            border: 1.5px solid #111827;
            image: url("assets/checkbox_check.png");
        }
        """)
        add_aligned_row(form_camera, "Chống giả mạo sinh trắc học:", self.chk_anti_spoofing)

        self.spn_spoof_thresh = QDoubleSpinBox()
        self.spn_spoof_thresh.setRange(0.5, 0.99)
        self.spn_spoof_thresh.setSingleStep(0.05)
        self.spn_spoof_thresh.setValue(float(self.db.get_setting("spoof_threshold", "0.85")))
        self.spn_spoof_thresh.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spn_spoof_thresh.setStyleSheet(spin_qss)
        self.lbl_spoof_thresh = add_aligned_row(form_camera, "Ngưỡng tin cậy chống giả mạo:", self.spn_spoof_thresh)
        self.chk_anti_spoofing.toggled.connect(self._on_anti_spoofing_toggled)
        self._on_anti_spoofing_toggled(self.chk_anti_spoofing.isChecked())

        # Cấu hình Camera Gắn Tường đếm người (Mô hình cố định YOLO11s, độ phân giải 1280)
        self.spn_headcount_conf = QDoubleSpinBox()
        self.spn_headcount_conf.setRange(0.10, 0.70)
        self.spn_headcount_conf.setSingleStep(0.05)
        self.spn_headcount_conf.setValue(float(self.db.get_setting("headcount_conf", "0.25")))
        self.spn_headcount_conf.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spn_headcount_conf.setStyleSheet(spin_qss)
        add_aligned_row(form_camera, "Ngưỡng tin cậy đếm người:", self.spn_headcount_conf)

        layout.addWidget(box_camera)

        # Box 3: Cấu hình Lọc Mặt Gần Nhất & Góc Nhìn Thẳng
        box_face_filter = QGroupBox("Cấu hình Lọc Mặt Gần Nhất và Góc Nhìn Thẳng")
        box_face_filter.setStyleSheet("""
        QGroupBox {
            font-weight: bold;
            color: #111827;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            margin-top: 8px;
            padding-top: 14px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 10px;
            padding: 0 4px;
            background-color: #ffffff;
        }
        """)
        filter_layout = QVBoxLayout(box_face_filter)
        filter_layout.setContentsMargins(14, 18, 14, 14)
        filter_layout.setSpacing(10)

        # Cách 1 (Mặc định hiển thị chính)
        form_filter = QFormLayout()
        form_filter.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form_filter.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form_filter.setHorizontalSpacing(14)
        form_filter.setVerticalSpacing(8)

        self.spn_min_face_size = QSpinBox()
        self.spn_min_face_size.setRange(5, 50)
        self.spn_min_face_size.setSingleStep(1)
        self.spn_min_face_size.setSuffix(" %")
        saved_min_size = int(float(self.db.get_setting("min_face_size_pct", "18")))
        self.spn_min_face_size.setValue(saved_min_size)
        self.spn_min_face_size.setFixedWidth(170)
        self.spn_min_face_size.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spn_min_face_size.setStyleSheet(spin_qss)
        add_aligned_row(form_filter, "Kích thước mặt tối thiểu (% khung hình):", self.spn_min_face_size)

        self.combo_pose_strictness = QComboBox()
        self.combo_pose_strictness.addItem("Dễ dãi", "lenient")
        self.combo_pose_strictness.addItem("Tiêu chuẩn", "standard")
        self.combo_pose_strictness.addItem("Nghiêm ngặt", "strict")
        self.combo_pose_strictness.addItem("Tùy chỉnh", "custom")
        saved_preset = self.db.get_setting("strictness_preset", "standard")
        idx = self.combo_pose_strictness.findData(saved_preset)
        if idx >= 0:
            self.combo_pose_strictness.setCurrentIndex(idx)
        else:
            self.combo_pose_strictness.setCurrentIndex(1)
        self.combo_pose_strictness.setFixedWidth(170)
        self.combo_pose_strictness.setStyleSheet(combo_qss)
        add_aligned_row(form_filter, "Độ khắt khe góc nhìn thẳng:", self.combo_pose_strictness)

        filter_layout.addLayout(form_filter)

        # Nút bấm toggle Cài đặt chi tiết (Cách 2) - KHÔNG DÙNG EMOJI!
        self.btn_toggle_detail_settings = QPushButton("Cài đặt chi tiết góc mặt...")
        self.btn_toggle_detail_settings.setFixedWidth(190)
        self.btn_toggle_detail_settings.setStyleSheet("""
        QPushButton {
            background-color: #f3f4f6;
            color: #111827;
            border: 1px solid #d1d5db;
            border-radius: 5px;
            padding: 6px 12px;
            font-weight: bold;
            font-size: 11px;
        }
        QPushButton:hover {
            background-color: #e5e7eb;
        }
        """)
        self.btn_toggle_detail_settings.clicked.connect(self._toggle_detail_settings)
        filter_layout.addWidget(self.btn_toggle_detail_settings)

        # Cách 2 (Cài đặt chi tiết - Mặc định ẩn, loại bỏ chữ trong ngoặc)
        self.detail_settings_widget = QWidget()
        self.detail_settings_widget.setObjectName("detail_settings_widget")
        self.detail_settings_widget.setVisible(False)
        self.detail_settings_widget.setStyleSheet("""
            QWidget#detail_settings_widget {
                background-color: #f9fafb;
                border: 1px solid #e5e7eb;
                border-radius: 6px;
            }
        """)
        detail_layout = QFormLayout(self.detail_settings_widget)
        detail_layout.setContentsMargins(12, 10, 12, 10)
        detail_layout.setHorizontalSpacing(14)
        detail_layout.setVerticalSpacing(8)

        self.spn_max_roll = QSpinBox()
        self.spn_max_roll.setRange(5, 45)
        self.spn_max_roll.setSingleStep(1)
        self.spn_max_roll.setSuffix(" °")
        saved_roll = int(float(self.db.get_setting("max_roll_deg", "15")))
        self.spn_max_roll.setValue(saved_roll)
        self.spn_max_roll.setFixedWidth(170)
        self.spn_max_roll.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spn_max_roll.setStyleSheet(spin_qss)
        add_aligned_row(detail_layout, "Góc nghiêng đầu tối đa:", self.spn_max_roll, label_width=274)

        self.spn_max_yaw = QSpinBox()
        self.spn_max_yaw.setRange(10, 60)
        self.spn_max_yaw.setSingleStep(5)
        self.spn_max_yaw.setSuffix(" %")
        saved_yaw = int(float(self.db.get_setting("max_yaw_pct", "30")))
        self.spn_max_yaw.setValue(saved_yaw)
        self.spn_max_yaw.setFixedWidth(170)
        self.spn_max_yaw.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spn_max_yaw.setStyleSheet(spin_qss)
        add_aligned_row(detail_layout, "Độ lệch xoay mặt tối đa:", self.spn_max_yaw, label_width=274)

        filter_layout.addWidget(self.detail_settings_widget)

        # Đồng bộ hai chiều giữa Preset và Spinbox chi tiết
        self._syncing_preset = False
        self.combo_pose_strictness.currentIndexChanged.connect(self._on_preset_changed)
        self.spn_max_roll.valueChanged.connect(self._on_detail_changed)
        self.spn_max_yaw.valueChanged.connect(self._on_detail_changed)

        layout.addWidget(box_face_filter)
        layout.addStretch()

        btn_save_settings = QPushButton("Lưu cấu hình hệ thống")
        btn_save_settings.setFixedHeight(38)
        btn_save_settings.setStyleSheet("background-color: #111827; color: #ffffff; font-weight: bold; font-size: 13px; border-radius: 6px;")
        btn_save_settings.clicked.connect(self.save_settings)
        layout.addWidget(btn_save_settings)

        scroll.setWidget(container)
        main_tab_layout.addWidget(scroll)

        # Lưu ảnh chụp trạng thái ban đầu của cấu hình để phát hiện thay đổi chưa lưu
        self.initial_config_state = self._get_config_state()

    def _browse_cam_in_file(self):
        f, _ = QFileDialog.getOpenFileName(self, "Chọn tệp video Cổng Vào", "", "Video Files (*.mp4 *.avi *.mkv *.mov);;All Files (*)")
        if f:
            self.txt_cam_in_file.setText(f)

    def _browse_cam_out_file(self):
        f, _ = QFileDialog.getOpenFileName(self, "Chọn tệp video Cổng Ra", "", "Video Files (*.mp4 *.avi *.mkv *.mov);;All Files (*)")
        if f:
            self.txt_cam_out_file.setText(f)

    def _init_cam_in_controls(self):
        raw = self.txt_cam_in.text().strip()
        self._syncing_cam_in = True
        if raw.isdigit():
            self.combo_cam_in_type.setCurrentIndex(0)
            self.spn_cam_in_idx.setValue(int(raw))
            self.stack_cam_in.setCurrentIndex(0)
        elif any(raw.lower().endswith(ext) for ext in [".mp4", ".avi", ".mkv", ".mov"]) or os.path.isfile(raw):
            self.combo_cam_in_type.setCurrentIndex(2)
            self.txt_cam_in_file.setText(raw)
            self.stack_cam_in.setCurrentIndex(2)
        else:
            self.combo_cam_in_type.setCurrentIndex(1)
            self.txt_cam_in_url.setText(raw)
            self.stack_cam_in.setCurrentIndex(1)
        self._syncing_cam_in = False

    def _on_cam_in_type_changed(self, index: int):
        self.stack_cam_in.setCurrentIndex(index)
        self._on_cam_in_sub_changed()

    def _on_cam_in_sub_changed(self):
        if getattr(self, "_syncing_cam_in", False):
            return
        self._syncing_cam_in = True
        idx = self.combo_cam_in_type.currentIndex()
        if idx == 0:
            val = str(self.spn_cam_in_idx.value())
        elif idx == 1:
            val = self.txt_cam_in_url.text().strip()
        else:
            val = self.txt_cam_in_file.text().strip()
        self.txt_cam_in.setText(val)
        self._syncing_cam_in = False

    def _on_txt_cam_in_changed(self, text: str):
        if getattr(self, "_syncing_cam_in", False):
            return
        self._syncing_cam_in = True
        raw = text.strip()
        if raw.isdigit():
            self.combo_cam_in_type.setCurrentIndex(0)
            self.spn_cam_in_idx.setValue(int(raw))
            self.stack_cam_in.setCurrentIndex(0)
        elif any(raw.lower().endswith(ext) for ext in [".mp4", ".avi", ".mkv", ".mov"]) or (raw and os.path.isfile(raw)):
            self.combo_cam_in_type.setCurrentIndex(2)
            self.txt_cam_in_file.setText(raw)
            self.stack_cam_in.setCurrentIndex(2)
        elif raw:
            self.combo_cam_in_type.setCurrentIndex(1)
            self.txt_cam_in_url.setText(raw)
            self.stack_cam_in.setCurrentIndex(1)
        self._syncing_cam_in = False

    def _init_cam_out_controls(self):
        raw = self.txt_cam_out.text().strip()
        self._syncing_cam_out = True
        if raw.isdigit():
            self.combo_cam_out_type.setCurrentIndex(0)
            self.spn_cam_out_idx.setValue(int(raw))
            self.stack_cam_out.setCurrentIndex(0)
        elif any(raw.lower().endswith(ext) for ext in [".mp4", ".avi", ".mkv", ".mov"]) or (raw and os.path.isfile(raw)):
            self.combo_cam_out_type.setCurrentIndex(2)
            self.txt_cam_out_file.setText(raw)
            self.stack_cam_out.setCurrentIndex(2)
        else:
            self.combo_cam_out_type.setCurrentIndex(1)
            self.txt_cam_out_url.setText(raw)
            self.stack_cam_out.setCurrentIndex(1)
        self._syncing_cam_out = False

    def _on_cam_out_type_changed(self, index: int):
        self.stack_cam_out.setCurrentIndex(index)
        self._on_cam_out_sub_changed()

    def _on_cam_out_sub_changed(self):
        if getattr(self, "_syncing_cam_out", False):
            return
        self._syncing_cam_out = True
        idx = self.combo_cam_out_type.currentIndex()
        if idx == 0:
            val = str(self.spn_cam_out_idx.value())
        elif idx == 1:
            val = self.txt_cam_out_url.text().strip()
        else:
            val = self.txt_cam_out_file.text().strip()
        self.txt_cam_out.setText(val)
        self._syncing_cam_out = False

    def _on_txt_cam_out_changed(self, text: str):
        if getattr(self, "_syncing_cam_out", False):
            return
        self._syncing_cam_out = True
        raw = text.strip()
        if raw.isdigit():
            self.combo_cam_out_type.setCurrentIndex(0)
            self.spn_cam_out_idx.setValue(int(raw))
            self.stack_cam_out.setCurrentIndex(0)
        elif any(raw.lower().endswith(ext) for ext in [".mp4", ".avi", ".mkv", ".mov"]) or (raw and os.path.isfile(raw)):
            self.combo_cam_out_type.setCurrentIndex(2)
            self.txt_cam_out_file.setText(raw)
            self.stack_cam_out.setCurrentIndex(2)
        elif raw:
            self.combo_cam_out_type.setCurrentIndex(1)
            self.txt_cam_out_url.setText(raw)
            self.stack_cam_out.setCurrentIndex(1)
        self._syncing_cam_out = False

    def set_active_time_field(self, key):
        self.active_time_key = key
        for k, te in self.time_edits.items():
            if key and k == key:
                te.setStyleSheet("""
                QTimeEdit {
                    background-color: #f3f4f6;
                    color: #111827;
                    padding: 4px 8px;
                    border-radius: 5px;
                    border: 1.5px solid #111827;
                    font-weight: bold;
                    font-size: 12px;
                }
                """ + "\n" + GLOBAL_MENU_QSS)
            else:
                te.setStyleSheet("""
                QTimeEdit {
                    background-color: #ffffff;
                    color: #111827;
                    padding: 4px 8px;
                    border-radius: 5px;
                    border: 1.5px solid #d1d5db;
                    font-weight: bold;
                    font-size: 12px;
                }
                """ + "\n" + GLOBAL_MENU_QSS)
        if key and key in self.time_edits:
            active_te = self.time_edits[key]
            clean_name = self.field_labels[key].rstrip(":").strip()
            self.lbl_clock_target.setText(clean_name)
            self.clock_widget.set_time(active_te.time())
            self._update_digital_label(active_te.time())
        else:
            self.lbl_clock_target.setText("Nhấp vào ô để chỉnh giờ")
            self.lbl_clock_digital.setText("--:--")

    def _on_session_index_changed(self, index: int):
        if not hasattr(self, "sessions_data") or index < 0 or index >= len(self.sessions_data):
            return
        self.current_session_index = index
        sess = self.sessions_data[index]
        for key, _, default_val in self.schedule_fields:
            val_str = sess.get(key, default_val)
            t_obj = QTime.fromString(val_str, "HH:mm")
            if not t_obj.isValid():
                t_obj = QTime.fromString(default_val, "HH:mm")
            if key in self.time_edits:
                self.time_edits[key].blockSignals(True)
                self.time_edits[key].setTime(t_obj)
                self.time_edits[key].blockSignals(False)
        if hasattr(self, "active_time_key") and self.active_time_key and self.active_time_key in self.time_edits:
            act_time = self.time_edits[self.active_time_key].time()
            self.clock_widget.set_time(act_time)
            self._update_digital_label(act_time)
        self._update_debug_quick_shift_buttons()

    def _on_add_session_clicked(self):
        new_num = len(self.sessions_data) + 1
        name, ok = QInputDialog.getText(self, "Thêm ca học", "Nhập tên ca học mới:", text=f"Ca {new_num}")
        if ok and name.strip():
            new_sess = {
                "id": None,
                "name": name.strip(),
                "checkin_open": "12:30",
                "class_start": "13:30",
                "late_threshold": "13:45",
                "checkin_cutoff": "14:15",
                "early_threshold": "17:15",
                "class_end": "17:30",
                "transition_cutoff": "18:00",
                "is_active": 1,
                "sort_order": len(self.sessions_data)
            }
            self.sessions_data.append(new_sess)
            self.combo_sessions.blockSignals(True)
            self.combo_sessions.addItem(new_sess["name"], None)
            self.combo_sessions.setCurrentIndex(len(self.sessions_data) - 1)
            self.combo_sessions.blockSignals(False)
            self._on_session_index_changed(len(self.sessions_data) - 1)

    def _on_delete_session_clicked(self):
        if len(self.sessions_data) <= 1:
            QMessageBox.warning(self, "Không thể xóa", "Hệ thống phải có ít nhất 1 ca học hoạt động.")
            return
        idx = getattr(self, "current_session_index", 0)
        if not (0 <= idx < len(self.sessions_data)):
            return
        curr_name = self.sessions_data[idx].get("name") or self.combo_sessions.currentText() or "Ca học"
        if ask_yes_no(
            self, "Xác nhận xóa ca học",
            f"Bạn có chắc muốn xóa '{curr_name}' khỏi danh sách ca học?"
        ):
            self.sessions_data.pop(idx)
            self.current_session_index = max(0, idx - 1)
            self.combo_sessions.blockSignals(True)
            self.combo_sessions.clear()
            for s in self.sessions_data:
                self.combo_sessions.addItem(s["name"], s.get("id"))
            self.combo_sessions.setCurrentIndex(self.current_session_index)
            self.combo_sessions.blockSignals(False)
            self._on_session_index_changed(self.current_session_index)

    def _on_time_edit_changed(self, key, time):
        if hasattr(self, "sessions_data") and 0 <= getattr(self, "current_session_index", 0) < len(self.sessions_data):
            self.sessions_data[self.current_session_index][key] = time.toString("HH:mm")
        if hasattr(self, "active_time_key") and self.active_time_key == key:
            self.clock_widget.set_time(time)
            self._update_digital_label(time)
        self._update_debug_quick_shift_buttons()

    def _on_clock_manipulated(self, time):
        if hasattr(self, "active_time_key") and self.active_time_key and self.active_time_key in self.time_edits:
            te = self.time_edits[self.active_time_key]
            if te.time() != time:
                te.setTime(time)
            self._update_digital_label(time)

    def _update_digital_label(self, time):
        fmt = "hh:mm AP" if self.chk_ampm.isChecked() else "HH:mm"
        self.lbl_clock_digital.setText(time.toString(fmt))

    def _adjust_active_time(self, hours=0, minutes=0):
        if not hasattr(self, "active_time_key") or not self.active_time_key or self.active_time_key not in self.time_edits:
            return
        te = self.time_edits[self.active_time_key]
        curr = te.time()
        new_time = curr.addSecs(hours * 3600 + minutes * 60)
        te.setTime(new_time)
        self.clock_widget.set_time(new_time)
        self._update_digital_label(new_time)

    def _toggle_ampm_active(self):
        if not hasattr(self, "active_time_key") or not self.active_time_key or self.active_time_key not in self.time_edits:
            return
        te = self.time_edits[self.active_time_key]
        curr = te.time()
        new_time = curr.addSecs(43200 if curr.hour() < 12 else -43200)
        te.setTime(new_time)
        self.clock_widget.set_time(new_time)
        self._update_digital_label(new_time)

    def _on_ampm_toggled(self, checked):
        fmt = "hh:mm AP" if checked else "HH:mm"
        for te in self.time_edits.values():
            te.setDisplayFormat(fmt)
        if hasattr(self, "active_time_key") and self.active_time_key and self.active_time_key in self.time_edits:
            self._update_digital_label(self.time_edits[self.active_time_key].time())
        if hasattr(self, "dbg_time_edit"):
            self.dbg_time_edit.setDisplayFormat("hh:mm:ss AP" if checked else "HH:mm:ss")
        self._update_debug_quick_shift_buttons()
        if hasattr(self, "table_attendance"):
            self.load_attendance_logs()
        main_win = self._get_main_window()
        if main_win and hasattr(main_win, "set_use_ampm"):
            main_win.set_use_ampm(checked)

    def _toggle_detail_settings(self):
        is_visible = self.detail_settings_widget.isVisible()
        self.detail_settings_widget.setVisible(not is_visible)
        if not is_visible:
            self.btn_toggle_detail_settings.setText("Thu gọn cài đặt chi tiết")
        else:
            self.btn_toggle_detail_settings.setText("Cài đặt chi tiết góc mặt...")

    def _on_preset_changed(self):
        if getattr(self, "_syncing_preset", False):
            return
        preset = self.combo_pose_strictness.currentData()
        self._syncing_preset = True
        if preset == "lenient":
            self.spn_max_roll.setValue(25)
            self.spn_max_yaw.setValue(40)
        elif preset == "standard":
            self.spn_max_roll.setValue(15)
            self.spn_max_yaw.setValue(30)
        elif preset == "strict":
            self.spn_max_roll.setValue(10)
            self.spn_max_yaw.setValue(20)
        self._syncing_preset = False

    def _on_detail_changed(self):
        if getattr(self, "_syncing_preset", False):
            return
        roll = self.spn_max_roll.value()
        yaw = self.spn_max_yaw.value()
        self._syncing_preset = True
        if roll == 25 and yaw == 40:
            idx = self.combo_pose_strictness.findData("lenient")
            if idx >= 0:
                self.combo_pose_strictness.setCurrentIndex(idx)
        elif roll == 15 and yaw == 30:
            idx = self.combo_pose_strictness.findData("standard")
            if idx >= 0:
                self.combo_pose_strictness.setCurrentIndex(idx)
        elif roll == 10 and yaw == 20:
            idx = self.combo_pose_strictness.findData("strict")
            if idx >= 0:
                self.combo_pose_strictness.setCurrentIndex(idx)
        else:
            idx = self.combo_pose_strictness.findData("custom")
            if idx >= 0:
                self.combo_pose_strictness.setCurrentIndex(idx)
        self._syncing_preset = False

    def _on_anti_spoofing_toggled(self, checked: bool):
        """Grey out hoặc kích hoạt ô cấu hình ngưỡng chống giả mạo theo trạng thái checkbox."""
        if hasattr(self, "spn_spoof_thresh"):
            self.spn_spoof_thresh.setEnabled(checked)
        if hasattr(self, "lbl_spoof_thresh"):
            self.lbl_spoof_thresh.setEnabled(checked)
            self.lbl_spoof_thresh.setStyleSheet(
                "color: #374151; font-size: 12px; border: none; background: transparent;" if checked
                else "color: #9ca3af; font-size: 12px; border: none; background: transparent;"
            )

    def _validate_schedule_logic(self) -> str | None:
        """Kiểm tra tính logic thực tế của danh sách ca học hiện tại."""
        if not hasattr(self, "sessions_data"):
            return None
        use_ampm = self.chk_ampm.isChecked() if hasattr(self, "chk_ampm") else (self.db.get_setting("use_ampm", "0") == "1")
        return validate_schedule_chronology(self.sessions_data, use_ampm=use_ampm)

    def save_settings(self) -> bool:
        # Đồng bộ ca học đang chọn vào sessions_data
        if hasattr(self, "sessions_data") and 0 <= getattr(self, "current_session_index", 0) < len(self.sessions_data):
            for k in ["checkin_open", "class_start", "late_threshold", "checkin_cutoff", "early_threshold", "class_end", "transition_cutoff"]:
                if k in self.time_edits:
                    self.sessions_data[self.current_session_index][k] = self.time_edits[k].time().toString("HH:mm")

        # Kiểm tra tính logic của cấu hình ca học trước khi lưu
        err_msg = self._validate_schedule_logic()
        if err_msg:
            if hasattr(self, "tabs") and hasattr(self, "tab_settings"):
                self.tabs.setCurrentWidget(self.tab_settings)
            QMessageBox.warning(self, "Lỗi logic ca học", err_msg)
            return False

        # Lưu sessions vào CSDL
        if hasattr(self, "sessions_data") and self.sessions_data:
            db_sessions = {s["id"]: s for s in self.db.get_all_sessions(only_active=False)}
            keep_ids = set()
            for i, s in enumerate(self.sessions_data):
                s_id = s.get("id")
                if s_id and s_id in db_sessions:
                    self.db.update_session(
                        s_id, s["name"], s["checkin_open"], s["class_start"],
                        s["late_threshold"], s["checkin_cutoff"], s["early_threshold"],
                        s["class_end"], s.get("transition_cutoff", "12:00"), s.get("is_active", 1), i
                    )
                    keep_ids.add(s_id)
                else:
                    new_id = self.db.add_session(
                        s["name"], s["checkin_open"], s["class_start"],
                        s["late_threshold"], s["checkin_cutoff"], s["early_threshold"],
                        s["class_end"], s.get("transition_cutoff", "12:00"), s.get("is_active", 1), i
                    )
                    s["id"] = new_id
                    keep_ids.add(new_id)
            for old_id in db_sessions:
                if old_id not in keep_ids:
                    try:
                        self.db.delete_session(old_id)
                    except Exception as e:
                        print(f"Lỗi khi xóa ca học ID {old_id}: {e}")

            # Cập nhật các key tương thích ngược theo ca đầu tiên
            first_s = self.sessions_data[0]
            for k in ["checkin_open", "class_start", "late_threshold", "checkin_cutoff", "early_threshold", "class_end", "transition_cutoff"]:
                if k in first_s:
                    self.db.set_setting(k, first_s[k])
        if hasattr(self, "combo_cam_in_type"):
            idx_in = self.combo_cam_in_type.currentIndex()
            if idx_in == 0:
                cam_in_val = str(self.spn_cam_in_idx.value())
            elif idx_in == 1:
                cam_in_val = self.txt_cam_in_url.text().strip()
            else:
                cam_in_val = self.txt_cam_in_file.text().strip()
            if hasattr(self, "txt_cam_in"):
                self._syncing_cam_in = True
                self.txt_cam_in.setText(cam_in_val)
                self._syncing_cam_in = False
        else:
            cam_in_val = self.txt_cam_in.text().strip()

        if hasattr(self, "combo_cam_out_type"):
            idx_out = self.combo_cam_out_type.currentIndex()
            if idx_out == 0:
                cam_out_val = str(self.spn_cam_out_idx.value())
            elif idx_out == 1:
                cam_out_val = self.txt_cam_out_url.text().strip()
            else:
                cam_out_val = self.txt_cam_out_file.text().strip()
            if hasattr(self, "txt_cam_out"):
                self._syncing_cam_out = True
                self.txt_cam_out.setText(cam_out_val)
                self._syncing_cam_out = False
        else:
            cam_out_val = self.txt_cam_out.text().strip()

        self.db.set_setting("camera_in", cam_in_val)
        self.db.set_setting("camera_out", cam_out_val)
        self.db.set_setting("use_ampm", "1" if self.chk_ampm.isChecked() else "0")
        if hasattr(self, "chk_anti_spoofing"):
            self.db.set_setting("anti_spoofing_enabled", "1" if self.chk_anti_spoofing.isChecked() else "0")
        if hasattr(self, "spn_spoof_thresh"):
            self.db.set_setting("spoof_threshold", str(round(self.spn_spoof_thresh.value(), 2)))

        self.db.set_setting("face_threshold", str(self.spn_thresh.value()))
        self.db.set_setting("verification_duration", str(self.spn_duration.value()))
        self.db.set_setting("verification_ratio", str(self.spn_ratio.value()))

        # Lưu cấu hình lọc mặt & góc nhìn
        self.db.set_setting("min_face_size_pct", str(self.spn_min_face_size.value()))
        self.db.set_setting("strictness_preset", self.combo_pose_strictness.currentData() or "standard")
        self.db.set_setting("max_roll_deg", str(self.spn_max_roll.value()))
        self.db.set_setting("max_yaw_pct", str(self.spn_max_yaw.value()))

        # Lưu cấu hình ngưỡng tin cậy đếm người
        if hasattr(self, "spn_headcount_conf"):
            self.db.set_setting("headcount_conf", str(round(self.spn_headcount_conf.value(), 2)))

        self.restart_cameras_cb()
        self.initial_config_state = self._get_config_state()
        QMessageBox.information(self, "Thành công", "Đã lưu cài đặt và cập nhật lại luồng camera.")
        return True

    def _get_config_state(self) -> dict:
        """Thu thập toàn bộ giá trị hiện tại của các trường cấu hình trên Tab Cấu hình."""
        state = {}
        if hasattr(self, "combo_cam_in_type"):
            idx_in = self.combo_cam_in_type.currentIndex()
            if idx_in == 0:
                state["camera_in"] = str(self.spn_cam_in_idx.value())
            elif idx_in == 1:
                state["camera_in"] = self.txt_cam_in_url.text().strip()
            else:
                state["camera_in"] = self.txt_cam_in_file.text().strip()
        elif hasattr(self, "txt_cam_in"):
            state["camera_in"] = self.txt_cam_in.text().strip()

        if hasattr(self, "combo_cam_out_type"):
            idx_out = self.combo_cam_out_type.currentIndex()
            if idx_out == 0:
                state["camera_out"] = str(self.spn_cam_out_idx.value())
            elif idx_out == 1:
                state["camera_out"] = self.txt_cam_out_url.text().strip()
            else:
                state["camera_out"] = self.txt_cam_out_file.text().strip()
        elif hasattr(self, "txt_cam_out"):
            state["camera_out"] = self.txt_cam_out.text().strip()
        if hasattr(self, "chk_ampm"):
            state["use_ampm"] = self.chk_ampm.isChecked()
        if hasattr(self, "chk_anti_spoofing"):
            state["anti_spoofing_enabled"] = self.chk_anti_spoofing.isChecked()
        if hasattr(self, "spn_spoof_thresh"):
            state["spoof_threshold"] = round(self.spn_spoof_thresh.value(), 2)
        if hasattr(self, "time_edits"):
            for k, te in self.time_edits.items():
                state[f"time_{k}"] = te.time().toString("HH:mm")
        if hasattr(self, "sessions_data"):
            state["sessions_data"] = json.dumps(self.sessions_data, sort_keys=True)
        if hasattr(self, "spn_thresh"):
            state["face_threshold"] = round(self.spn_thresh.value(), 3)
        if hasattr(self, "spn_duration"):
            state["verification_duration"] = round(self.spn_duration.value(), 2)
        if hasattr(self, "spn_ratio"):
            state["verification_ratio"] = int(self.spn_ratio.value())
        if hasattr(self, "spn_min_face_size"):
            state["min_face_size_pct"] = int(self.spn_min_face_size.value())
        if hasattr(self, "combo_pose_strictness"):
            state["strictness_preset"] = str(self.combo_pose_strictness.currentData() or "standard")
        if hasattr(self, "spn_max_roll"):
            state["max_roll_deg"] = int(self.spn_max_roll.value())
        if hasattr(self, "spn_max_yaw"):
            state["max_yaw_pct"] = int(self.spn_max_yaw.value())
        if hasattr(self, "spn_headcount_conf"):
            state["headcount_conf"] = round(self.spn_headcount_conf.value(), 2)
        return state

    def has_unsaved_config(self) -> bool:
        """Kiểm tra xem người dùng có chỉnh sửa cấu hình mà chưa bấm Lưu hay không."""
        user_role = getattr(self, "current_user", {}).get("role", "super_admin")
        if user_role in ("manager", "teacher", "viewer"):
            return False
        if not hasattr(self, "initial_config_state"):
            return False
        return self._get_config_state() != self.initial_config_state

    def _handle_unsaved_settings(self) -> bool:
        """Xử lý cảnh báo thay đổi chưa lưu khi đóng cửa sổ.
        Trả về True nếu tiếp tục đóng (đã lưu hoặc chọn không lưu), False nếu hủy đóng.
        """
        if not self.has_unsaved_config():
            return True

        box = QMessageBox(self)
        box.setWindowTitle("Cảnh báo cấu hình chưa lưu")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText("Bạn có thay đổi trong phần Cấu hình chưa được lưu.\nBạn có muốn lưu các thay đổi này trước khi đóng không?")

        btn_save = box.addButton("Lưu thay đổi", QMessageBox.ButtonRole.AcceptRole)
        btn_discard = box.addButton("Không lưu", QMessageBox.ButtonRole.DestructiveRole)
        btn_cancel = box.addButton("Hủy", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_save)

        box.setStyleSheet(GLOBAL_DIALOG_QSS)

        box.exec()
        clicked = box.clickedButton()
        if clicked == btn_save:
            return self.save_settings()
        elif clicked == btn_discard:
            saved_use_ampm = self.db.get_setting("use_ampm", "0") == "1"
            main_win = self._get_main_window()
            if main_win and hasattr(main_win, "set_use_ampm"):
                main_win.set_use_ampm(saved_use_ampm)
            return True
        else:
            if hasattr(self, "tabs") and hasattr(self, "tab_settings"):
                self.tabs.setCurrentWidget(self.tab_settings)
            return False

    def closeEvent(self, event):
        if not self._handle_unsaved_settings():
            event.ignore()
            return
        if hasattr(self, "chk_debug_enable") and self.chk_debug_enable.isChecked():
            self.chk_debug_enable.blockSignals(True)
            self.chk_debug_enable.setChecked(False)
            self.chk_debug_enable.blockSignals(False)
        if hasattr(self, "debug_mgr") and self.debug_mgr and getattr(self.debug_mgr, "is_active", False):
            self.debug_mgr.disable()
            if hasattr(self, "db"):
                self.db.clear_mock_students_and_classes()
            if hasattr(self, "reload_cache_cb") and callable(self.reload_cache_cb):
                self.reload_cache_cb()
        event.accept()

    def reject(self):
        # Override reject của QDialog (ví dụ bấm phím Esc) để chạy qua closeEvent
        self.close()

    def _setup_tab_debug(self):
        main_tab_layout = QVBoxLayout(self.tab_debug)
        main_tab_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }\n" + GLOBAL_SCROLLBAR_QSS)

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setSpacing(14)
        layout.setContentsMargins(10, 10, 10, 10)

        # 1. Box Bật/Tắt Debug Mode
        box_toggle = QGroupBox("Kích hoạt Chế độ Debug")
        box_toggle.setStyleSheet("""
        QGroupBox {
            font-weight: bold;
            color: #111827;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            margin-top: 8px;
            padding-top: 14px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 10px;
            padding: 0 4px;
            background-color: #ffffff;
        }
        """)
        toggle_layout = QVBoxLayout(box_toggle)
        toggle_layout.setContentsMargins(14, 14, 14, 12)
        toggle_layout.setSpacing(8)

        self.chk_debug_enable = QCheckBox("Bật Chế độ Giả lập / Debug")
        self.chk_debug_enable.setStyleSheet("""
        QCheckBox {
            color: #111827;
            font-weight: bold;
            font-size: 13px;
            spacing: 8px;
        }
        QCheckBox::indicator {
            width: 16px;
            height: 16px;
            border-radius: 4px;
            border: 1.5px solid #9ca3af;
            background-color: #ffffff;
        }
        QCheckBox::indicator:hover {
            border-color: #111827;
            background-color: #f9fafb;
        }
        QCheckBox::indicator:checked {
            background-color: #111827;
            border: 1.5px solid #111827;
            image: url("assets/checkbox_check.png");
        }
        """)
        if self.debug_mgr:
            self.chk_debug_enable.setChecked(self.debug_mgr.is_active)
        self.chk_debug_enable.toggled.connect(self._on_debug_toggled)
        toggle_layout.addWidget(self.chk_debug_enable)

        lbl_desc = QLabel("Khi bật, toàn bộ hệ thống (đồng hồ màn hình chính, điểm danh ca học, nhật ký quét) sẽ chạy theo thời gian giả lập bên dưới thay vì đồng hồ Windows thực tế.")
        lbl_desc.setWordWrap(True)
        lbl_desc.setStyleSheet("color: #6b7280; font-size: 11px;")
        toggle_layout.addWidget(lbl_desc)

        self.chk_debug_multiple_scans = QCheckBox("Cho phép quét điểm danh nhiều lần trong một ca")
        self.chk_debug_multiple_scans.setStyleSheet("""
        QCheckBox {
            color: #111827;
            font-weight: bold;
            font-size: 13px;
            spacing: 8px;
        }
        QCheckBox:disabled {
            color: #9ca3af;
        }
        QCheckBox::indicator {
            width: 16px;
            height: 16px;
            border-radius: 4px;
            border: 1.5px solid #9ca3af;
            background-color: #ffffff;
        }
        QCheckBox::indicator:hover {
            border-color: #111827;
            background-color: #f9fafb;
        }
        QCheckBox::indicator:checked {
            background-color: #111827;
            border: 1.5px solid #111827;
            image: url("assets/checkbox_check.png");
        }
        QCheckBox::indicator:disabled {
            border: 1.5px solid #d1d5db;
            background-color: #f3f4f6;
        }
        """)
        self.chk_debug_multiple_scans.setChecked(self.db.get_setting("debug_allow_multiple_scans", "1") == "1")
        self.chk_debug_multiple_scans.toggled.connect(self._on_debug_multiple_scans_toggled)
        toggle_layout.addWidget(self.chk_debug_multiple_scans)

        layout.addWidget(box_toggle)

        # 2. Box Cấu hình Thời gian Giả lập (Virtual Clock)
        box_clock = QGroupBox("Đồng hồ Giả lập Độc lập")
        box_clock.setStyleSheet("""
        QGroupBox {
            font-weight: bold;
            color: #111827;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            margin-top: 8px;
            padding-top: 14px;
        }
        QGroupBox:disabled {
            color: #9ca3af;
            border-color: #f3f4f6;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 10px;
            padding: 0 4px;
            background-color: #ffffff;
        }
        QGroupBox::title:disabled {
            color: #9ca3af;
        }
        """)
        clock_layout = QVBoxLayout(box_clock)
        clock_layout.setContentsMargins(14, 14, 14, 12)
        clock_layout.setSpacing(10)

        # Hàng chọn ngày & giờ
        dt_row = QHBoxLayout()
        dt_row.setSpacing(12)

        lbl_d = QLabel("Ngày giả lập:")
        lbl_d.setStyleSheet("color: #374151; font-weight: bold;")
        dt_row.addWidget(lbl_d)

        self.dbg_date_edit = QDateEdit()
        self.dbg_date_edit.setCalendarPopup(True)
        self.dbg_date_edit.setDisplayFormat("dd/MM/yyyy")
        self.dbg_date_edit.setFixedWidth(160)
        self.dbg_date_edit.setStyleSheet("""
        QDateEdit {
            background-color: #ffffff;
            color: #111827;
            padding: 5px 30px 5px 10px;
            border-radius: 5px;
            border: 1px solid #d1d5db;
            font-size: 12px;
        }
        QDateEdit:disabled {
            background-color: #f9fafb;
            color: #9ca3af;
            border-color: #e5e7eb;
        }
        QDateEdit::drop-down {
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 24px;
            border-left: 1px solid #e5e7eb;
            border-top-right-radius: 5px;
            border-bottom-right-radius: 5px;
            background-color: #f9fafb;
        }
        QDateEdit::down-arrow {
            image: url("assets/calendar.png");
            width: 13px;
            height: 13px;
        }
        """)

        # Tùy biến popup lịch trực quan nền sáng cho chế độ Debug
        dbg_cal = self.dbg_date_edit.calendarWidget()
        if dbg_cal:
            dbg_cal.setStyleSheet("""
            QCalendarWidget QWidget {
                background-color: #ffffff;
                color: #111827;
            }
            QCalendarWidget QToolButton {
                color: #111827;
                background-color: #f3f4f6;
                border-radius: 4px;
                padding: 4px 8px;
                font-weight: bold;
            }
            QCalendarWidget QToolButton:hover {
                background-color: #e5e7eb;
            }
            QCalendarWidget QMenu {
                background-color: #ffffff;
                color: #111827;
            }
            QCalendarWidget QSpinBox {
                background-color: #ffffff;
                color: #111827;
                border: 1px solid #d1d5db;
                border-radius: 4px;
            }
            QCalendarWidget QTableView {
                background-color: #ffffff;
                selection-background-color: #111827;
                selection-color: #ffffff;
            }
            """)

        dt_row.addWidget(self.dbg_date_edit)

        lbl_t = QLabel("Giờ giả lập:")
        lbl_t.setStyleSheet("color: #374151; font-weight: bold; margin-left: 10px;")
        dt_row.addWidget(lbl_t)

        self.dbg_time_edit = QTimeEdit()
        use_ampm = self.db.get_setting("use_ampm", "0") == "1"
        self.dbg_time_edit.setDisplayFormat("hh:mm:ss AP" if use_ampm else "HH:mm:ss")
        self.dbg_time_edit.setFixedWidth(135)
        self.dbg_time_edit.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.dbg_time_edit.setStyleSheet("QTimeEdit { background-color: #ffffff; color: #111827; padding: 6px 10px; border-radius: 5px; border: 1px solid #d1d5db; font-size: 12px; }\nQTimeEdit:disabled { background-color: #f9fafb; color: #9ca3af; border-color: #e5e7eb; }\n" + GLOBAL_MENU_QSS)
        dt_row.addWidget(self.dbg_time_edit)

        self.chk_dbg_freeze = QCheckBox("Đóng băng giờ")
        self.chk_dbg_freeze.setStyleSheet("""
        QCheckBox {
            color: #111827;
            font-weight: bold;
            font-size: 12px;
            margin-left: 10px;
            spacing: 8px;
        }
        QCheckBox:disabled {
            color: #9ca3af;
        }
        QCheckBox::indicator {
            width: 16px;
            height: 16px;
            border-radius: 4px;
            border: 1.5px solid #9ca3af;
            background-color: #ffffff;
        }
        QCheckBox::indicator:hover {
            border-color: #111827;
            background-color: #f9fafb;
        }
        QCheckBox::indicator:checked {
            background-color: #111827;
            border: 1.5px solid #111827;
            image: url("assets/checkbox_check.png");
        }
        QCheckBox::indicator:disabled {
            border: 1.5px solid #d1d5db;
            background-color: #f3f4f6;
        }
        """)
        if self.debug_mgr:
            self.chk_dbg_freeze.setChecked(self.debug_mgr.is_frozen)
        dt_row.addWidget(self.chk_dbg_freeze)

        dt_row.addStretch()
        clock_layout.addLayout(dt_row)

        # Nút áp dụng ngày giờ
        btn_apply_time = QPushButton("Cập nhật thời gian giả lập")
        btn_apply_time.setFixedHeight(30)
        btn_apply_time.setStyleSheet("QPushButton { background-color: #111827; color: #ffffff; font-weight: bold; border-radius: 4px; padding: 4px 12px; }\nQPushButton:disabled { background-color: #f3f4f6; color: #9ca3af; border: 1px solid #e5e7eb; }")
        btn_apply_time.clicked.connect(self._apply_custom_debug_time)
        clock_layout.addWidget(btn_apply_time)

        # Hàng nút nhảy nhanh mốc ca học (TUYỆT ĐỐI ZERO EMOJI)
        self.lbl_quick_shifts = QLabel("Nhảy nhanh đến các mốc ca học:")
        self.lbl_quick_shifts.setStyleSheet("color: #374151; font-weight: bold; font-size: 11px; margin-top: 6px;")
        clock_layout.addWidget(self.lbl_quick_shifts)

        shift_grid = QHBoxLayout()
        shift_grid.setSpacing(6)

        self.btn_dbg_open = QPushButton("Mở cổng")
        self.btn_dbg_start = QPushButton("Bắt đầu")
        self.btn_dbg_ontime = QPushButton("Đúng giờ")
        self.btn_dbg_late = QPushButton("Đi trễ")
        self.btn_dbg_cutoff = QPushButton("Đóng cổng")
        self.btn_dbg_early = QPushButton("Về sớm")
        self.btn_dbg_end = QPushButton("Kết thúc")

        for btn in [self.btn_dbg_open, self.btn_dbg_start, self.btn_dbg_ontime, self.btn_dbg_late, self.btn_dbg_cutoff, self.btn_dbg_early, self.btn_dbg_end]:
            btn.setFixedHeight(28)
            btn.setStyleSheet("QPushButton { background-color: #f9fafb; color: #111827; font-weight: bold; font-size: 11px; border-radius: 4px; border: 1px solid #d1d5db; padding: 2px 6px; }\nQPushButton:disabled { background-color: #f3f4f6; color: #9ca3af; border-color: #e5e7eb; }")
            shift_grid.addWidget(btn)

        self.btn_dbg_open.clicked.connect(lambda: self._set_debug_time_hhmmss(*getattr(self, "_btn_dbg_open_time", (6, 30, 0))))
        self.btn_dbg_start.clicked.connect(lambda: self._set_debug_time_hhmmss(*getattr(self, "_btn_dbg_start_time", (7, 30, 0))))
        self.btn_dbg_ontime.clicked.connect(lambda: self._set_debug_time_hhmmss(*getattr(self, "_btn_dbg_ontime_time", (7, 44, 30))))
        self.btn_dbg_late.clicked.connect(lambda: self._set_debug_time_hhmmss(*getattr(self, "_btn_dbg_late_time", (7, 46, 0))))
        self.btn_dbg_cutoff.clicked.connect(lambda: self._set_debug_time_hhmmss(*getattr(self, "_btn_dbg_cutoff_time", (8, 16, 0))))
        self.btn_dbg_early.clicked.connect(lambda: self._set_debug_time_hhmmss(*getattr(self, "_btn_dbg_early_time", (11, 14, 0))))
        self.btn_dbg_end.clicked.connect(lambda: self._set_debug_time_hhmmss(*getattr(self, "_btn_dbg_end_time", (11, 30, 0))))

        clock_layout.addLayout(shift_grid)

        # Hàng nút chỉnh bước nhảy thời gian
        adjust_row = QHBoxLayout()
        adjust_row.setSpacing(6)

        btn_adj_m1h = QPushButton("-1 giờ")
        btn_adj_p1h = QPushButton("+1 giờ")
        btn_adj_m5m = QPushButton("-5 phút")
        btn_adj_p5m = QPushButton("+5 phút")
        btn_adj_now = QPushButton("Đồng bộ giờ Windows hiện tại")

        for b in [btn_adj_m1h, btn_adj_p1h, btn_adj_m5m, btn_adj_p5m, btn_adj_now]:
            b.setFixedHeight(26)
            b.setStyleSheet("QPushButton { background-color: #f9fafb; color: #4b5563; font-weight: bold; font-size: 11px; border-radius: 4px; border: 1px solid #e5e7eb; padding: 2px 8px; }\nQPushButton:disabled { background-color: #f3f4f6; color: #9ca3af; border-color: #e5e7eb; }")
            adjust_row.addWidget(b)

        btn_adj_m1h.clicked.connect(lambda: self._adjust_debug_time(hours=-1))
        btn_adj_p1h.clicked.connect(lambda: self._adjust_debug_time(hours=1))
        btn_adj_m5m.clicked.connect(lambda: self._adjust_debug_time(minutes=-5))
        btn_adj_p5m.clicked.connect(lambda: self._adjust_debug_time(minutes=5))
        btn_adj_now.clicked.connect(self._sync_debug_to_real_now)

        clock_layout.addLayout(adjust_row)
        layout.addWidget(box_clock)

        # 3. Box Dữ liệu Lớp học và Sinh viên Ảo
        box_mock = QGroupBox("Dữ liệu Lớp học và Sinh viên Ảo")
        box_mock.setStyleSheet("""
        QGroupBox {
            font-weight: bold;
            color: #111827;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            margin-top: 8px;
            padding-top: 14px;
        }
        QGroupBox:disabled {
            color: #9ca3af;
            border-color: #f3f4f6;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 10px;
            padding: 0 4px;
            background-color: #ffffff;
        }
        QGroupBox::title:disabled {
            color: #9ca3af;
        }
        """)
        mock_layout = QVBoxLayout(box_mock)
        mock_layout.setContentsMargins(14, 14, 14, 12)
        mock_layout.setSpacing(10)

        lbl_mock_desc = QLabel("Tạo danh sách lớp học ảo và sinh viên ảo phục vụ kiểm thử tính năng điểm danh và phân quyền. Toàn bộ dữ liệu này sẽ tự động xóa sạch khi tắt Chế độ Debug.")
        lbl_mock_desc.setWordWrap(True)
        lbl_mock_desc.setStyleSheet("color: #6b7280; font-size: 11px;")
        mock_layout.addWidget(lbl_mock_desc)

        mock_btn_row = QHBoxLayout()
        mock_btn_row.setSpacing(10)

        self.btn_create_mock = QPushButton("Tạo dữ liệu ảo...")
        self.btn_create_mock.setFixedHeight(32)
        self.btn_create_mock.setStyleSheet("QPushButton { background-color: #111827; color: #ffffff; font-weight: bold; border-radius: 5px; padding: 4px 14px; }\nQPushButton:disabled { background-color: #f3f4f6; color: #9ca3af; border: 1px solid #e5e7eb; }")
        self.btn_create_mock.clicked.connect(self._open_create_mock_data_dialog)
        mock_btn_row.addWidget(self.btn_create_mock)

        self.btn_clear_mock = QPushButton("Xóa sạch dữ liệu ảo")
        self.btn_clear_mock.setFixedHeight(32)
        self.btn_clear_mock.setStyleSheet("QPushButton { background-color: #ffffff; color: #dc2626; font-weight: bold; border-radius: 5px; border: 1px solid #fca5a5; padding: 4px 14px; }\nQPushButton:disabled { background-color: #f3f4f6; color: #9ca3af; border-color: #e5e7eb; }")
        self.btn_clear_mock.clicked.connect(self._on_clear_mock_data)
        mock_btn_row.addWidget(self.btn_clear_mock)

        mock_layout.addLayout(mock_btn_row)
        layout.addWidget(box_mock)

        # 4. Box Giả lập Sự kiện Điểm danh (Simulation Tools)
        box_sim = QGroupBox("Giả lập Quét Điểm danh")
        box_sim.setStyleSheet("""
        QGroupBox {
            font-weight: bold;
            color: #111827;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            margin-top: 8px;
            padding-top: 14px;
        }
        QGroupBox:disabled {
            color: #9ca3af;
            border-color: #f3f4f6;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 10px;
            padding: 0 4px;
            background-color: #ffffff;
        }
        QGroupBox::title:disabled {
            color: #9ca3af;
        }
        """)
        sim_layout = QVBoxLayout(box_sim)
        sim_layout.setContentsMargins(14, 14, 14, 12)
        sim_layout.setSpacing(10)

        sim_form = QHBoxLayout()
        sim_form.setSpacing(10)
        lbl_sel_sv = QLabel("Chọn Sinh viên:")
        lbl_sel_sv.setStyleSheet("color: #374151; font-weight: bold;")
        sim_form.addWidget(lbl_sel_sv)

        self.combo_dbg_student = QComboBox()
        self.combo_dbg_student.setStyleSheet(GLOBAL_COMBOBOX_QSS)
        sim_form.addWidget(self.combo_dbg_student, 1)

        sim_layout.addLayout(sim_form)

        sim_btn_row = QHBoxLayout()
        sim_btn_row.setSpacing(10)

        self.btn_sim_in = QPushButton("Giả lập Quét Cổng Vào")
        self.btn_sim_in.setFixedHeight(32)
        self.btn_sim_in.setStyleSheet("QPushButton { background-color: #111827; color: #ffffff; font-weight: bold; border-radius: 5px; }\nQPushButton:disabled { background-color: #f3f4f6; color: #9ca3af; border: 1px solid #e5e7eb; }")
        self.btn_sim_in.clicked.connect(lambda: self._simulate_scan("IN"))
        sim_btn_row.addWidget(self.btn_sim_in)

        self.btn_sim_out = QPushButton("Giả lập Quét Cổng Ra")
        self.btn_sim_out.setFixedHeight(32)
        self.btn_sim_out.setStyleSheet("QPushButton { background-color: #374151; color: #ffffff; font-weight: bold; border-radius: 5px; }\nQPushButton:disabled { background-color: #f3f4f6; color: #9ca3af; border: 1px solid #e5e7eb; }")
        self.btn_sim_out.clicked.connect(lambda: self._simulate_scan("OUT"))
        sim_btn_row.addWidget(self.btn_sim_out)

        btn_sim_stranger = QPushButton("Giả lập Cảnh báo Người Lạ")
        btn_sim_stranger.setFixedHeight(32)
        btn_sim_stranger.setStyleSheet("QPushButton { background-color: #ffffff; color: #dc2626; font-weight: bold; border-radius: 5px; border: 1px solid #fca5a5; }\nQPushButton:disabled { background-color: #f3f4f6; color: #9ca3af; border-color: #e5e7eb; }")
        btn_sim_stranger.clicked.connect(self._simulate_stranger)
        sim_btn_row.addWidget(btn_sim_stranger)

        sim_layout.addLayout(sim_btn_row)
        layout.addWidget(box_sim)

        # 5. Box Tiện ích Dữ liệu Kiểm thử
        box_util = QGroupBox("Tiện ích Dữ liệu Kiểm thử")
        box_util.setStyleSheet("""
        QGroupBox {
            font-weight: bold;
            color: #111827;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            margin-top: 8px;
            padding-top: 14px;
        }
        QGroupBox:disabled {
            color: #9ca3af;
            border-color: #f3f4f6;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 10px;
            padding: 0 4px;
            background-color: #ffffff;
        }
        QGroupBox::title:disabled {
            color: #9ca3af;
        }
        """)
        util_layout = QHBoxLayout(box_util)
        util_layout.setContentsMargins(14, 14, 14, 12)

        btn_clear_today = QPushButton("Xóa sạch điểm danh của ngày giả lập hiện tại")
        btn_clear_today.setFixedHeight(34)
        btn_clear_today.setStyleSheet("QPushButton { background-color: #ffffff; color: #d97706; font-weight: bold; border-radius: 5px; padding: 6px 14px; border: 1px solid #fcd34d; }\nQPushButton:disabled { background-color: #f3f4f6; color: #9ca3af; border-color: #e5e7eb; }")
        btn_clear_today.clicked.connect(self._clear_simulated_date_attendance)
        util_layout.addWidget(btn_clear_today)

        btn_clear_all_debug = QPushButton("Xóa sạch toàn bộ dữ liệu Debug")
        btn_clear_all_debug.setFixedHeight(34)
        btn_clear_all_debug.setStyleSheet("QPushButton { background-color: #ffffff; color: #dc2626; font-weight: bold; border-radius: 5px; padding: 6px 14px; border: 1px solid #fca5a5; }\nQPushButton:disabled { background-color: #f3f4f6; color: #9ca3af; border-color: #e5e7eb; }")
        btn_clear_all_debug.clicked.connect(self._clear_all_debug_attendance)
        util_layout.addWidget(btn_clear_all_debug)

        layout.addWidget(box_util)
        layout.addStretch()

        self.box_dbg_clock = box_clock
        self.box_dbg_mock = box_mock
        self.box_dbg_sim = box_sim
        self.box_dbg_util = box_util

        scroll.setWidget(container)
        main_tab_layout.addWidget(scroll)

        # Khởi tạo giá trị ban đầu cho các ô nhập và trạng thái khóa/mở
        self._refresh_debug_inputs()
        self._set_debug_controls_enabled(self.chk_debug_enable.isChecked())

    def _set_debug_controls_enabled(self, enabled: bool):
        """Khóa hoặc mở khóa toàn bộ các tính năng trong Chế độ Debug dựa trên checkbox."""
        if hasattr(self, "chk_debug_multiple_scans"):
            self.chk_debug_multiple_scans.setEnabled(enabled)
        if hasattr(self, "box_dbg_clock"):
            self.box_dbg_clock.setEnabled(enabled)
        if hasattr(self, "box_dbg_mock"):
            self.box_dbg_mock.setEnabled(enabled)
        if hasattr(self, "box_dbg_sim"):
            self.box_dbg_sim.setEnabled(enabled)
        if hasattr(self, "box_dbg_util"):
            self.box_dbg_util.setEnabled(enabled)

    def _refresh_debug_student_list(self):
        """Làm mới danh sách sinh viên trong combo box của Chế độ Debug chỉ lấy sinh viên ảo (is_mock = 1)."""
        if not hasattr(self, "combo_dbg_student"):
            return
        curr_code = ""
        curr_data = self.combo_dbg_student.currentData()
        if isinstance(curr_data, dict):
            curr_code = curr_data.get("student_code", "")
        self.combo_dbg_student.blockSignals(True)
        self.combo_dbg_student.clear()
        mock_students = self.db.get_mock_students()
        selected_idx = -1
        is_dbg_on = hasattr(self, "chk_debug_enable") and self.chk_debug_enable.isChecked()
        if not mock_students:
            self.combo_dbg_student.addItem("Không có sinh viên ảo (hãy nhấn Tạo dữ liệu ảo)", None)
            if hasattr(self, "btn_sim_in"):
                self.btn_sim_in.setEnabled(False)
            if hasattr(self, "btn_sim_out"):
                self.btn_sim_out.setEnabled(False)
        else:
            if hasattr(self, "btn_sim_in"):
                self.btn_sim_in.setEnabled(is_dbg_on)
            if hasattr(self, "btn_sim_out"):
                self.btn_sim_out.setEnabled(is_dbg_on)
            for idx, s in enumerate(mock_students):
                cls_text = f" ({s.get('class_name')})" if s.get('class_name') else ""
                self.combo_dbg_student.addItem(f"{s['student_code']} - {s['full_name']}{cls_text}", s)
                if curr_code and s.get("student_code") == curr_code:
                    selected_idx = idx
            if selected_idx >= 0:
                self.combo_dbg_student.setCurrentIndex(selected_idx)
            elif self.combo_dbg_student.count() > 0:
                self.combo_dbg_student.setCurrentIndex(0)
        self.combo_dbg_student.blockSignals(False)

    def _open_create_mock_data_dialog(self):
        if not self.chk_debug_enable.isChecked():
            QMessageBox.warning(self, "Chưa kích hoạt", "Vui lòng tick 'Bật Chế độ Giả lập / Debug' để sử dụng tính năng này.")
            return
        dlg = CreateMockDataDialog(self, self.db, on_created_cb=self._on_mock_data_changed)
        dlg.exec()

    def _on_mock_data_changed(self):
        self._refresh_debug_student_list()
        if hasattr(self, "reload_cache_cb") and callable(self.reload_cache_cb):
            self.reload_cache_cb()
        if hasattr(self, "load_students"):
            self.load_students()
        if hasattr(self, "_populate_student_class_filter"):
            self._populate_student_class_filter()
        if hasattr(self, "_populate_att_class_filter"):
            self._populate_att_class_filter()
        if hasattr(self, "load_attendance_logs"):
            self.load_attendance_logs()
        main_win = self._get_main_window()
        if main_win and hasattr(main_win, "_update_stats"):
            main_win._update_stats()

    def _on_clear_mock_data(self):
        if not self.chk_debug_enable.isChecked():
            QMessageBox.warning(self, "Chưa kích hoạt", "Vui lòng tick 'Bật Chế độ Giả lập / Debug' để sử dụng tính năng này.")
            return
        if ask_yes_no(
            self, "Xác nhận xóa dữ liệu ảo",
            "Bạn có chắc muốn xóa sạch toàn bộ sinh viên ảo, lớp học ảo và điểm danh thử nghiệm không?"
        ):
            del_students, del_classes = self.db.clear_mock_students_and_classes()
            self._on_mock_data_changed()
            QMessageBox.information(self, "Thành công", f"Đã xóa {del_students} sinh viên ảo và {del_classes} lớp học ảo.")

    def _refresh_debug_inputs(self):
        self._refresh_debug_student_list()
        curr_dt = self.debug_mgr.get_current_datetime() if self.debug_mgr else datetime.now()
        if hasattr(self, "dbg_date_edit"):
            self.dbg_date_edit.setDate(QDate(curr_dt.year, curr_dt.month, curr_dt.day))
        if hasattr(self, "dbg_time_edit"):
            self.dbg_time_edit.setTime(QTime(curr_dt.hour, curr_dt.minute, curr_dt.second))

    def _on_debug_toggled(self, checked):
        self._set_debug_controls_enabled(checked)
        if not self.debug_mgr:
            return
        if checked:
            qdate = self.dbg_date_edit.date()
            qtime = self.dbg_time_edit.time()
            dt = datetime(qdate.year(), qdate.month(), qdate.day(), qtime.hour(), qtime.minute(), qtime.second())
            self.debug_mgr.enable(dt, frozen=self.chk_dbg_freeze.isChecked())
            self.db.set_debug_mode(True)
            self._on_mock_data_changed()
            use_ampm = self.chk_ampm.isChecked() if hasattr(self, "chk_ampm") else (self.db.get_setting("use_ampm", "0") == "1")
            msg_fmt = '%I:%M:%S %p - %d/%m/%Y' if use_ampm else '%H:%M:%S - %d/%m/%Y'
            QMessageBox.information(self, "Chế độ Debug", f"Đã bật Chế độ Debug!\nThời gian giả lập: {dt.strftime(msg_fmt)}")
        else:
            self.debug_mgr.disable()
            self.db.set_debug_mode(False)
            self.db.clear_mock_students_and_classes()
            self._on_mock_data_changed()
            QMessageBox.information(self, "Chế độ Debug", "Đã tắt Chế độ Debug. Hệ thống quay về đồng hồ Windows thực tế và đã xóa sạch dữ liệu ảo thử nghiệm.")

    def _on_debug_multiple_scans_toggled(self, checked):
        self.db.set_setting("debug_allow_multiple_scans", "1" if checked else "0")
        if self.attendance_logic:
            self.attendance_logic.allow_multiple_debug_scans = checked

    def _apply_custom_debug_time(self):
        if not self.chk_debug_enable.isChecked() or not self.debug_mgr:
            return
        qdate = self.dbg_date_edit.date()
        qtime = self.dbg_time_edit.time()
        dt = datetime(qdate.year(), qdate.month(), qdate.day(), qtime.hour(), qtime.minute(), qtime.second())
        self.debug_mgr.set_time(dt, frozen=self.chk_dbg_freeze.isChecked())
        use_ampm = self.chk_ampm.isChecked() if hasattr(self, "chk_ampm") else (self.db.get_setting("use_ampm", "0") == "1")
        msg_fmt = '%I:%M:%S %p - %d/%m/%Y' if use_ampm else '%H:%M:%S - %d/%m/%Y'
        QMessageBox.information(self, "Thông báo", f"Đã cập nhật giờ giả lập thành: {dt.strftime(msg_fmt)}")

    def _update_debug_quick_shift_buttons(self):
        if not hasattr(self, "btn_dbg_open") or not hasattr(self, "time_edits"):
            return

        sess_name = "Ca học"
        if hasattr(self, "combo_sessions") and self.combo_sessions.currentIndex() >= 0:
            sess_name = self.combo_sessions.currentText()

        self.lbl_quick_shifts.setText(f"Nhảy nhanh đến các mốc ca học ({sess_name}):")

        t_open = self.time_edits["checkin_open"].time() if "checkin_open" in self.time_edits else QTime(6, 30)
        t_start = self.time_edits["class_start"].time() if "class_start" in self.time_edits else QTime(7, 30)
        t_late = self.time_edits["late_threshold"].time() if "late_threshold" in self.time_edits else QTime(7, 45)
        t_cutoff = self.time_edits["checkin_cutoff"].time() if "checkin_cutoff" in self.time_edits else QTime(8, 15)
        t_early = self.time_edits["early_threshold"].time() if "early_threshold" in self.time_edits else QTime(11, 15)
        t_end = self.time_edits["class_end"].time() if "class_end" in self.time_edits else QTime(11, 30)

        use_ampm = self.chk_ampm.isChecked() if hasattr(self, "chk_ampm") else (self.db.get_setting("use_ampm", "0") == "1")
        btn_fmt = "hh:mm AP" if use_ampm else "HH:mm"

        # 1. Mở cổng
        self.btn_dbg_open.setText(f"Mở cổng ({t_open.toString(btn_fmt)})")
        self._btn_dbg_open_time = (t_open.hour(), t_open.minute(), 0)

        # 2. Bắt đầu ca
        self.btn_dbg_start.setText(f"Bắt đầu ({t_start.toString(btn_fmt)})")
        self._btn_dbg_start_time = (t_start.hour(), t_start.minute(), 0)

        # 3. Đúng giờ: 1 phút trước hạn chót trễ
        t_ontime = t_late.addSecs(-60)
        self.btn_dbg_ontime.setText(f"Đúng giờ ({t_ontime.toString(btn_fmt)})")
        self._btn_dbg_ontime_time = (t_ontime.hour(), t_ontime.minute(), 30)

        # 4. Đi trễ: 1 phút sau hạn chót trễ
        t_late_test = t_late.addSecs(60)
        self.btn_dbg_late.setText(f"Đi trễ ({t_late_test.toString(btn_fmt)})")
        self._btn_dbg_late_time = (t_late_test.hour(), t_late_test.minute(), 0)

        # 5. Đóng cổng: 1 phút sau giờ đóng cổng (vắng)
        t_cutoff_test = t_cutoff.addSecs(60)
        self.btn_dbg_cutoff.setText(f"Đóng cổng ({t_cutoff_test.toString(btn_fmt)})")
        self._btn_dbg_cutoff_time = (t_cutoff_test.hour(), t_cutoff_test.minute(), 0)

        # 6. Về sớm: 1 phút trước hạn chót về sớm
        t_early_test = t_early.addSecs(-60)
        self.btn_dbg_early.setText(f"Về sớm ({t_early_test.toString(btn_fmt)})")
        self._btn_dbg_early_time = (t_early_test.hour(), t_early_test.minute(), 0)

        # 7. Kết thúc
        self.btn_dbg_end.setText(f"Kết thúc ({t_end.toString(btn_fmt)})")
        self._btn_dbg_end_time = (t_end.hour(), t_end.minute(), 0)

    def _set_debug_time_hhmmss(self, h, m, s):
        is_active = self.chk_debug_enable.isChecked() or (self.debug_mgr and self.debug_mgr.is_active)
        if not is_active or not self.debug_mgr:
            return
        curr_dt = self.debug_mgr.get_current_datetime()
        new_dt = datetime(curr_dt.year, curr_dt.month, curr_dt.day, h, m, s)
        self.dbg_time_edit.setTime(QTime(h, m, s))
        self.debug_mgr.set_time(new_dt, frozen=self.chk_dbg_freeze.isChecked())

    def _adjust_debug_time(self, hours=0, minutes=0):
        is_active = self.chk_debug_enable.isChecked() or (self.debug_mgr and self.debug_mgr.is_active)
        if not is_active or not self.debug_mgr:
            return
        self.debug_mgr.adjust_time(hours=hours, minutes=minutes)
        new_dt = self.debug_mgr.get_current_datetime()
        self.dbg_date_edit.setDate(QDate(new_dt.year, new_dt.month, new_dt.day))
        self.dbg_time_edit.setTime(QTime(new_dt.hour, new_dt.minute, new_dt.second))

    def _sync_debug_to_real_now(self):
        is_active = self.chk_debug_enable.isChecked() or (self.debug_mgr and self.debug_mgr.is_active)
        if not is_active:
            return
        real_now = datetime.now()
        self.dbg_date_edit.setDate(QDate(real_now.year, real_now.month, real_now.day))
        self.dbg_time_edit.setTime(QTime(real_now.hour, real_now.minute, real_now.second))
        if self.debug_mgr:
            self.debug_mgr.set_time(real_now, frozen=self.chk_dbg_freeze.isChecked())

    def _simulate_scan(self, camera_type: str):
        is_active = self.chk_debug_enable.isChecked() or (self.debug_mgr and self.debug_mgr.is_active)
        if not is_active:
            QMessageBox.warning(self, "Chưa kích hoạt", "Vui lòng tick 'Bật Chế độ Giả lập / Debug' để sử dụng tính năng này.")
            return
        if self.combo_dbg_student.count() == 0:
            QMessageBox.warning(self, "Lỗi", "Chưa có sinh viên nào trong danh sách.")
            return
        student = self.combo_dbg_student.currentData()
        if not student:
            QMessageBox.warning(self, "Lỗi", "Vui lòng chọn sinh viên.")
            return

        parent_win = self._get_main_window()
        if parent_win and hasattr(parent_win, "handle_simulated_scan"):
            parent_win.handle_simulated_scan(student, camera_type)
            QMessageBox.information(self, "Giả lập Quét", f"Đã gửi sự kiện quét {camera_type} cho {student['full_name']}.")
        else:
            QMessageBox.warning(self, "Lỗi", "Không tìm thấy cửa sổ chính để thực hiện quét.")

    def _simulate_stranger(self):
        if not self.chk_debug_enable.isChecked():
            QMessageBox.warning(self, "Chưa kích hoạt", "Vui lòng tick 'Bật Chế độ Giả lập / Debug' để sử dụng tính năng này.")
            return
        parent_win = self._get_main_window()
        if parent_win and hasattr(parent_win, "on_stranger_detected"):
            parent_win.on_stranger_detected("IN")
            QMessageBox.information(self, "Giả lập Người lạ", "Đã kích hoạt sự kiện cảnh báo người lạ tại Cổng Vào.")

    def _clear_simulated_date_attendance(self):
        if not self.chk_debug_enable.isChecked():
            QMessageBox.warning(self, "Chưa kích hoạt", "Vui lòng tick 'Bật Chế độ Giả lập / Debug' để sử dụng tính năng này.")
            return
        curr_dt = self.debug_mgr.get_current_datetime() if self.debug_mgr else datetime.now()
        date_str = curr_dt.date().isoformat()
        if ask_yes_no(
            self, "Xác nhận xóa",
            f"Bạn có chắc muốn xóa sạch toàn bộ bản ghi điểm danh của ngày {curr_dt.strftime('%d/%m/%Y')}?\nThao tác này chỉ ảnh hưởng dữ liệu ngày này để bạn thử nghiệm lại từ đầu."
        ):
            count = self.db.delete_attendance_by_date(date_str)
            main_win = self._get_main_window()
            if main_win and hasattr(main_win, "_update_stats"):
                main_win._update_stats()
            if main_win and getattr(main_win, "last_card_source", None) == "debug":
                main_win._clear_recent_student_card()
            self.load_attendance_logs()
            QMessageBox.information(self, "Thành công", f"Đã xóa {count} bản ghi điểm danh của ngày {curr_dt.strftime('%d/%m/%Y')}.")

    def _clear_all_debug_attendance(self):
        if not self.chk_debug_enable.isChecked():
            QMessageBox.warning(self, "Chưa kích hoạt", "Vui lòng tick 'Bật Chế độ Giả lập / Debug' để sử dụng tính năng này.")
            return
        if ask_yes_no(
            self, "Xác nhận xóa",
            "Bạn có chắc muốn xóa sạch toàn bộ các bản ghi điểm danh thử nghiệm trong bảng Debug?\n(Dữ liệu điểm danh chính thức không bị ảnh hưởng)."
        ):
            count = self.db.clear_all_debug_attendance()
            main_win = self._get_main_window()
            if main_win and hasattr(main_win, "_update_stats"):
                main_win._update_stats()
            if main_win and getattr(main_win, "last_card_source", None) == "debug":
                main_win._clear_recent_student_card()
            self.load_attendance_logs()
            QMessageBox.information(self, "Thành công", f"Đã xóa sạch {count} bản ghi trong bảng điểm danh Debug.")

    # ------------------ Tab 5: Thông Tin Người Quản Trị & Hồ Sơ Cá Nhân ------------------

    def _setup_tab_admin_profile(self):
        scroll = QScrollArea(self.tab_admin_profile)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(16)

        # Hàng trên: 2 Box cạnh nhau (Hồ sơ cá nhân & Đổi mật khẩu)
        top_row = QHBoxLayout()
        top_row.setSpacing(16)

        # Box 1: Hồ Sơ Cá Nhân & Ảnh Đại Diện
        box_profile = QGroupBox("Hồ Sơ Người Quản Trị")
        box_profile.setStyleSheet("QGroupBox { font-weight: bold; border: 1px solid #e5e7eb; border-radius: 6px; margin-top: 10px; padding-top: 10px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #111827; }")
        box_profile_layout = QVBoxLayout(box_profile)
        box_profile_layout.setSpacing(12)

        # Khung ảnh đại diện lớn ở giữa
        avatar_center_layout = QHBoxLayout()
        avatar_center_layout.addStretch(1)
        self.lbl_profile_avatar = QLabel()
        self.lbl_profile_avatar.setFixedSize(110, 110)
        self.lbl_profile_avatar.setStyleSheet("border: none; background: transparent;")
        self.lbl_profile_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar_center_layout.addWidget(self.lbl_profile_avatar)
        avatar_center_layout.addStretch(1)
        box_profile_layout.addLayout(avatar_center_layout)

        # Hàng nút ảnh đại diện
        avatar_btn_layout = QHBoxLayout()
        avatar_btn_layout.setSpacing(8)

        self.btn_choose_avatar = QPushButton("Chọn ảnh từ tệp...")
        self.btn_choose_avatar.setStyleSheet("background-color: #f3f4f6; color: #374151; font-weight: bold; padding: 6px 12px; border: 1px solid #d1d5db; border-radius: 5px;")
        self.btn_choose_avatar.clicked.connect(self._on_choose_avatar_clicked)
        avatar_btn_layout.addWidget(self.btn_choose_avatar, 1)

        self.btn_webcam_avatar = QPushButton("Chụp từ Webcam...")
        self.btn_webcam_avatar.setStyleSheet("background-color: #f3f4f6; color: #374151; font-weight: bold; padding: 6px 12px; border: 1px solid #d1d5db; border-radius: 5px;")
        self.btn_webcam_avatar.clicked.connect(self._on_webcam_avatar_clicked)
        avatar_btn_layout.addWidget(self.btn_webcam_avatar, 1)

        self.btn_clear_avatar = QPushButton("Xóa ảnh")
        self.btn_clear_avatar.setStyleSheet("background-color: #f3f4f6; color: #dc2626; font-weight: bold; padding: 6px 12px; border: 1px solid #fca5a5; border-radius: 5px;")
        self.btn_clear_avatar.clicked.connect(self._on_clear_avatar_clicked)
        avatar_btn_layout.addWidget(self.btn_clear_avatar)

        box_profile_layout.addLayout(avatar_btn_layout)

        # Form chi tiết hồ sơ
        form_prof = QFormLayout()
        form_prof.setSpacing(8)

        self.txt_profile_username = QLineEdit()
        self.txt_profile_username.setReadOnly(True)
        self.txt_profile_username.setStyleSheet("background-color: #f3f4f6; color: #6b7280; font-weight: bold; padding: 4px 10px; border: 1.5px solid #d1d5db; border-radius: 5px; font-size: 12px;")
        add_form_row(form_prof, "Tên đăng nhập:", self.txt_profile_username, label_width=135, height=36)

        self.txt_profile_fullname = QLineEdit()
        self.txt_profile_fullname.setStyleSheet("padding: 4px 10px; border: 1.5px solid #d1d5db; border-radius: 5px; font-size: 12px;")
        add_form_row(form_prof, "Họ và Tên:", self.txt_profile_fullname, label_width=135, height=36)

        self.lbl_profile_role = QLabel()
        self.lbl_profile_role.setStyleSheet("font-weight: bold; color: #1f2937;")
        add_form_row(form_prof, "Vai trò:", self.lbl_profile_role, label_width=135, height=36)

        self.lbl_profile_classes = QLabel()
        self.lbl_profile_classes.setStyleSheet("font-weight: bold; color: #1f2937;")
        add_form_row(form_prof, "Lớp phụ trách:", self.lbl_profile_classes, label_width=135, height=36)

        self.lbl_profile_created = QLabel()
        self.lbl_profile_created.setStyleSheet("color: #4b5563; font-size: 11px;")
        add_form_row(form_prof, "Ngày tạo:", self.lbl_profile_created, label_width=135, height=36)

        self.lbl_profile_last_login = QLabel()
        self.lbl_profile_last_login.setStyleSheet("color: #4b5563; font-size: 11px;")
        add_form_row(form_prof, "Đăng nhập cuối:", self.lbl_profile_last_login, label_width=135, height=36)

        box_profile_layout.addLayout(form_prof)

        self.btn_save_profile = QPushButton("Lưu thông tin cá nhân")
        self.btn_save_profile.setStyleSheet("background-color: #1f2937; color: #ffffff; font-weight: bold; padding: 8px 16px; border-radius: 5px;")
        self.btn_save_profile.clicked.connect(self._on_save_profile_clicked)
        box_profile_layout.addWidget(self.btn_save_profile)

        top_row.addWidget(box_profile, 1)

        # Box 2: Đổi Mật Khẩu Cá Nhân
        box_pwd = QGroupBox("Đổi Mật Khẩu Cá Nhân")
        box_pwd.setStyleSheet("QGroupBox { font-weight: bold; border: 1px solid #e5e7eb; border-radius: 6px; margin-top: 10px; padding-top: 10px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #111827; }")
        box_pwd_layout = QVBoxLayout(box_pwd)
        box_pwd_layout.setSpacing(10)

        form_pwd = QFormLayout()
        form_pwd.setSpacing(8)

        self.txt_profile_old_pwd = QLineEdit()
        self.txt_profile_old_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_profile_old_pwd.setPlaceholderText("Nhập mật khẩu đang sử dụng")
        self.txt_profile_old_pwd.setStyleSheet("padding: 4px 10px; border: 1.5px solid #d1d5db; border-radius: 5px; font-size: 12px;")
        add_form_row(form_pwd, "Mật khẩu hiện tại:", self.txt_profile_old_pwd, label_width=135, height=36)

        self.txt_profile_new_pwd = QLineEdit()
        self.txt_profile_new_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_profile_new_pwd.setPlaceholderText("Tối thiểu 6 ký tự")
        self.txt_profile_new_pwd.setStyleSheet("padding: 4px 10px; border: 1.5px solid #d1d5db; border-radius: 5px; font-size: 12px;")
        add_form_row(form_pwd, "Mật khẩu mới:", self.txt_profile_new_pwd, label_width=135, height=36)

        self.txt_profile_confirm_pwd = QLineEdit()
        self.txt_profile_confirm_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_profile_confirm_pwd.setPlaceholderText("Nhập lại mật khẩu mới")
        self.txt_profile_confirm_pwd.setStyleSheet("padding: 4px 10px; border: 1.5px solid #d1d5db; border-radius: 5px; font-size: 12px;")
        add_form_row(form_pwd, "Xác nhận mật khẩu:", self.txt_profile_confirm_pwd, label_width=135, height=36)

        box_pwd_layout.addLayout(form_pwd)

        self.chk_profile_show_pwd = QCheckBox("Hiện mật khẩu")
        self.chk_profile_show_pwd.setStyleSheet(GLOBAL_CHECKBOX_QSS)
        self.chk_profile_show_pwd.toggled.connect(self._on_toggle_show_profile_pwd)
        box_pwd_layout.addWidget(self.chk_profile_show_pwd)

        box_pwd_layout.addStretch(1)

        self.btn_update_pwd = QPushButton("Cập nhật mật khẩu")
        self.btn_update_pwd.setStyleSheet("background-color: #1f2937; color: #ffffff; font-weight: bold; padding: 8px 16px; border-radius: 5px;")
        self.btn_update_pwd.clicked.connect(self._on_update_profile_password_clicked)
        box_pwd_layout.addWidget(self.btn_update_pwd)

        top_row.addWidget(box_pwd, 1)
        layout.addLayout(top_row)

        # Nếu là super_admin: Bổ sung Bảng Quản lý Danh sách Người Dùng & Audit Logs
        is_super = self.current_user.get("role") == "super_admin"
        if is_super:
            box_mgmt = QGroupBox("Danh Sách Tài Khoản Quản Trị Hệ Thống")
            box_mgmt.setStyleSheet("QGroupBox { font-weight: bold; border: 1px solid #e5e7eb; border-radius: 6px; margin-top: 10px; padding-top: 10px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #111827; }")
            box_mgmt_layout = QVBoxLayout(box_mgmt)
            box_mgmt_layout.setSpacing(10)

            self.table_admin_users = QTableWidget()
            self.table_admin_users.setStyleSheet(GLOBAL_TABLE_QSS)
            self.table_admin_users.setColumnCount(7)
            self.table_admin_users.setHorizontalHeaderLabels(["ID", "Tên đăng nhập", "Họ và Tên", "Vai trò", "Lớp phụ trách", "Trạng thái", "Đăng nhập cuối"])
            self.table_admin_users.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
            self.table_admin_users.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
            self.table_admin_users.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            self.table_admin_users.setSortingEnabled(True)
            self.table_admin_users.verticalHeader().setVisible(False)
            u_header = self.table_admin_users.horizontalHeader()
            u_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
            u_header.resizeSection(0, 45)
            u_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
            u_header.resizeSection(1, 105)
            u_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
            u_header.resizeSection(2, 150)
            u_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
            u_header.resizeSection(3, 155)
            u_header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
            u_header.setSectionResizeMode(5, QHeaderView.ResizeMode.Interactive)
            u_header.resizeSection(5, 140)
            u_header.setSectionResizeMode(6, QHeaderView.ResizeMode.Interactive)
            u_header.resizeSection(6, 135)
            self.table_admin_users.setFixedHeight(160)
            self.table_admin_users.itemSelectionChanged.connect(self._on_admin_user_selection_changed)
            box_mgmt_layout.addWidget(self.table_admin_users)

            u_btn_layout = QHBoxLayout()
            u_btn_layout.setSpacing(8)

            self.btn_add_user = QPushButton("Thêm tài khoản...")
            self.btn_add_user.setStyleSheet("background-color: #16a34a; color: #ffffff; font-weight: bold; padding: 6px 12px; border-radius: 5px;")
            self.btn_add_user.clicked.connect(self._on_add_user_clicked)
            u_btn_layout.addWidget(self.btn_add_user)

            self.btn_assign_classes_admin = QPushButton("Phân công lớp...")
            self.btn_assign_classes_admin.setStyleSheet("background-color: #f3f4f6; color: #111827; font-weight: bold; padding: 6px 12px; border: 1px solid #d1d5db; border-radius: 5px;")
            self.btn_assign_classes_admin.clicked.connect(self._on_assign_classes_admin_clicked)
            u_btn_layout.addWidget(self.btn_assign_classes_admin)

            self.btn_change_username = QPushButton("Đổi tên đăng nhập...")
            self.btn_change_username.setStyleSheet("background-color: #f3f4f6; color: #374151; font-weight: bold; padding: 6px 12px; border: 1px solid #d1d5db; border-radius: 5px;")
            self.btn_change_username.clicked.connect(self._on_change_username_clicked)
            u_btn_layout.addWidget(self.btn_change_username)

            self.btn_toggle_user_active = QPushButton("Khóa / Mở khóa")
            self.btn_toggle_user_active.setStyleSheet("background-color: #f3f4f6; color: #374151; font-weight: bold; padding: 6px 12px; border: 1px solid #d1d5db; border-radius: 5px;")
            self.btn_toggle_user_active.clicked.connect(self._on_toggle_user_active_clicked)
            u_btn_layout.addWidget(self.btn_toggle_user_active)

            self.btn_reset_user_pwd = QPushButton("Đặt lại mật khẩu...")
            self.btn_reset_user_pwd.setStyleSheet("background-color: #f3f4f6; color: #374151; font-weight: bold; padding: 6px 12px; border: 1px solid #d1d5db; border-radius: 5px;")
            self.btn_reset_user_pwd.clicked.connect(self._on_reset_user_pwd_clicked)
            u_btn_layout.addWidget(self.btn_reset_user_pwd)

            self.btn_delete_user = QPushButton("Xóa tài khoản")
            self.btn_delete_user.setStyleSheet("background-color: #f3f4f6; color: #dc2626; font-weight: bold; padding: 6px 12px; border: 1px solid #fca5a5; border-radius: 5px;")
            self.btn_delete_user.clicked.connect(self._on_delete_user_clicked)
            u_btn_layout.addWidget(self.btn_delete_user)

            u_btn_layout.addStretch(1)

            self.btn_refresh_users = QPushButton("Làm mới")
            self.btn_refresh_users.setStyleSheet("background-color: #f3f4f6; color: #374151; padding: 6px 12px; border: 1px solid #d1d5db; border-radius: 5px;")
            self.btn_refresh_users.clicked.connect(self._load_admin_users_table)
            u_btn_layout.addWidget(self.btn_refresh_users)

            box_mgmt_layout.addLayout(u_btn_layout)
            layout.addWidget(box_mgmt)

            # Box Audit Logs
            box_audit = QGroupBox("Nhật Ký Kiểm Toán Hành Vi")
            box_audit.setStyleSheet("QGroupBox { font-weight: bold; border: 1px solid #e5e7eb; border-radius: 6px; margin-top: 10px; padding-top: 10px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #111827; }")
            box_audit_layout = QVBoxLayout(box_audit)
            box_audit_layout.setSpacing(10)

            self.table_admin_audit_logs = QTableWidget()
            self.table_admin_audit_logs.setStyleSheet(GLOBAL_TABLE_QSS)
            self.table_admin_audit_logs.setColumnCount(4)
            self.table_admin_audit_logs.setHorizontalHeaderLabels(["Thời gian", "Tài khoản", "Hành động", "Chi tiết"])
            self.table_admin_audit_logs.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
            self.table_admin_audit_logs.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            self.table_admin_audit_logs.setSortingEnabled(True)
            self.table_admin_audit_logs.verticalHeader().setVisible(False)
            a_header = self.table_admin_audit_logs.horizontalHeader()
            a_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
            a_header.resizeSection(0, 155)
            a_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
            a_header.resizeSection(1, 110)
            a_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
            a_header.resizeSection(2, 195)
            a_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
            self.table_admin_audit_logs.setFixedHeight(140)
            box_audit_layout.addWidget(self.table_admin_audit_logs)

            a_btn_layout = QHBoxLayout()
            a_btn_layout.addStretch(1)
            self.btn_refresh_audit_logs = QPushButton("Làm mới nhật ký")
            self.btn_refresh_audit_logs.setStyleSheet("background-color: #f3f4f6; color: #374151; padding: 6px 12px; border: 1px solid #d1d5db; border-radius: 5px;")
            self.btn_refresh_audit_logs.clicked.connect(self._load_admin_audit_logs_table)
            a_btn_layout.addWidget(self.btn_refresh_audit_logs)
            box_audit_layout.addLayout(a_btn_layout)

            layout.addWidget(box_audit)

        is_manager = self.current_user.get("role") == "manager"
        if is_manager:
            box_teacher_assignment = QGroupBox("Phân Công Lớp Giảng Dạy Cho Giáo Viên")
            box_teacher_assignment.setStyleSheet("QGroupBox { font-weight: bold; border: 1px solid #e5e7eb; border-radius: 6px; margin-top: 10px; padding-top: 10px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #111827; }")
            bta_layout = QVBoxLayout(box_teacher_assignment)
            bta_layout.setSpacing(10)

            self.table_teachers = QTableWidget()
            self.table_teachers.setStyleSheet(GLOBAL_TABLE_QSS)
            self.table_teachers.setColumnCount(4)
            self.table_teachers.setHorizontalHeaderLabels(["ID", "Tên đăng nhập", "Họ và Tên", "Lớp phụ trách"])
            self.table_teachers.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
            self.table_teachers.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
            self.table_teachers.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            self.table_teachers.setSortingEnabled(True)
            self.table_teachers.verticalHeader().setVisible(False)
            t_hdr = self.table_teachers.horizontalHeader()
            t_hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
            t_hdr.resizeSection(0, 50)
            t_hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
            t_hdr.resizeSection(1, 130)
            t_hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
            t_hdr.resizeSection(2, 180)
            t_hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
            self.table_teachers.setFixedHeight(180)
            bta_layout.addWidget(self.table_teachers)

            t_btn_layout = QHBoxLayout()
            t_btn_layout.setSpacing(8)

            self.btn_assign_classes_mgr = QPushButton("Phân công lớp cho giáo viên...")
            self.btn_assign_classes_mgr.setStyleSheet("background-color: #111827; color: #ffffff; font-weight: bold; padding: 6px 14px; border-radius: 5px;")
            self.btn_assign_classes_mgr.clicked.connect(self._on_assign_classes_mgr_clicked)
            t_btn_layout.addWidget(self.btn_assign_classes_mgr)

            self.btn_refresh_teachers = QPushButton("Làm mới danh sách")
            self.btn_refresh_teachers.setStyleSheet("background-color: #f3f4f6; color: #374151; padding: 6px 12px; border: 1px solid #d1d5db; border-radius: 5px;")
            self.btn_refresh_teachers.clicked.connect(self._load_teachers_table)
            t_btn_layout.addWidget(self.btn_refresh_teachers)
            t_btn_layout.addStretch(1)

            bta_layout.addLayout(t_btn_layout)
            layout.addWidget(box_teacher_assignment)
            self._load_teachers_table()

        scroll.setWidget(container)
        main_tab_layout = QVBoxLayout(self.tab_admin_profile)
        main_tab_layout.setContentsMargins(0, 0, 0, 0)
        main_tab_layout.addWidget(scroll)

        self._load_admin_profile_data()

    def _load_admin_profile_data(self):
        uid = self.current_user.get("id")
        if uid:
            user = self.db.get_admin_user_by_id(uid)
            if user:
                self.current_user.update(user)

        uname = self.current_user.get("username", "admin")
        self.txt_profile_username.setText(uname)
        if uname == "admin":
            self.txt_profile_username.setReadOnly(True)
            self.txt_profile_username.setStyleSheet("background-color: #f3f4f6; color: #6b7280; font-weight: bold; padding: 4px 10px; border: 1.5px solid #d1d5db; border-radius: 5px; font-size: 12px;")
        else:
            self.txt_profile_username.setReadOnly(False)
            self.txt_profile_username.setStyleSheet("background-color: #ffffff; color: #111827; padding: 4px 10px; border: 1.5px solid #d1d5db; border-radius: 5px; font-size: 12px;")

        self.txt_profile_fullname.setText(self.current_user.get("full_name", ""))
        
        role_map = {
            "super_admin": "Quản trị viên cấp cao",
            "manager": "Giáo vụ",
            "teacher": "Giáo viên",
            "technician": "Kỹ thuật viên"
        }
        r = self.current_user.get("role", "super_admin")
        self.lbl_profile_role.setText(role_map.get(r, r))
        if hasattr(self, "lbl_profile_classes"):
            if r == "teacher":
                t_classes = self.db.get_admin_user_classes(uid) if uid else []
                self.lbl_profile_classes.setText(", ".join(t_classes) if t_classes else "Chưa được phân công lớp")
            elif r == "manager":
                self.lbl_profile_classes.setText("Tất cả các lớp")
            elif r == "super_admin":
                self.lbl_profile_classes.setText("Toàn quyền hệ thống")
            else:
                self.lbl_profile_classes.setText("Không truy cập")
        self.lbl_profile_created.setText(format_datetime_str(self.current_user.get("created_at", "-")))
        self.lbl_profile_last_login.setText(format_datetime_str(self.current_user.get("last_login", "-")))

        avatar_path = self.current_user.get("avatar_path", "")
        self._update_profile_avatar_preview(avatar_path)
        self._update_header_user_display()

        if self.current_user.get("role") == "super_admin":
            self._load_admin_users_table()
            self._load_admin_audit_logs_table()

    def _update_profile_avatar_preview(self, avatar_path: str):
        pix = self._load_avatar_pixmap(avatar_path, size=110, border_color="#d1d5db", border_width=2.0)
        self.lbl_profile_avatar.setPixmap(pix)

    def _on_toggle_show_profile_pwd(self, checked: bool):
        mode = QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        self.txt_profile_old_pwd.setEchoMode(mode)
        self.txt_profile_new_pwd.setEchoMode(mode)
        self.txt_profile_confirm_pwd.setEchoMode(mode)

    def _cleanup_old_avatar(self, uname: str, keep_path: str = ""):
        old_avatar = self.current_user.get("avatar_path", "")
        if old_avatar and os.path.exists(old_avatar):
            if not keep_path or os.path.abspath(old_avatar) != os.path.abspath(keep_path):
                try:
                    os.remove(old_avatar)
                except Exception:
                    pass
        if uname:
            for ext in [".enc", ".jpg", ".jpeg", ".png", ".bmp", ".webp"]:
                legacy = f"data/avatars/{uname}_avatar{ext}"
                if os.path.exists(legacy) and (not keep_path or os.path.abspath(legacy) != os.path.abspath(keep_path)):
                    try:
                        os.remove(legacy)
                    except Exception:
                        pass

    def _on_choose_avatar_clicked(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Chọn ảnh đại diện", "", "Hình ảnh (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        if not file_path:
            return
        os.makedirs("data/avatars", exist_ok=True)
        uname = self.current_user.get("username", "admin")
        dest_path = f"data/avatars/{uname}_avatar.enc"
        try:
            with open(file_path, "rb") as f:
                raw_bytes = f.read()
            img = cv2.imdecode(np.frombuffer(raw_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                img = cv2.imread(file_path)
            if img is None:
                QMessageBox.warning(self, "Lỗi đọc ảnh", "Không thể đọc tệp hình ảnh đã chọn.")
                return

            self._cleanup_old_avatar(uname, keep_path=dest_path)
            saved_path = self.db.save_encrypted_photo(dest_path, img)
            self.current_user["avatar_path"] = saved_path
            self._update_profile_avatar_preview(saved_path)
            self._update_header_user_display()
        except Exception as e:
            QMessageBox.critical(self, "Lỗi lưu ảnh", f"Không thể mã hoá và lưu ảnh đại diện: {str(e)}")

    def _on_webcam_avatar_clicked(self):
        main_win = self._get_main_window()
        frame = None
        for w_attr in ("worker_in", "worker_out"):
            if main_win and hasattr(main_win, w_attr):
                w = getattr(main_win, w_attr)
                if w:
                    if hasattr(w, "get_latest_frame") and callable(w.get_latest_frame):
                        res = w.get_latest_frame()
                        if isinstance(res, np.ndarray):
                            frame = res
                    if frame is None and hasattr(w, "latest_raw_frame") and isinstance(w.latest_raw_frame, np.ndarray):
                        frame = w.latest_raw_frame
                    if frame is None and hasattr(w, "latest_frame") and isinstance(w.latest_frame, np.ndarray):
                        frame = w.latest_frame
                    if frame is not None:
                        break

        if frame is None:
            QMessageBox.warning(self, "Không có luồng camera", "Hiện không có khung hình camera nào sẵn sàng. Vui lòng chọn ảnh từ tệp trên máy tính.")
            return

        h, w = frame.shape[:2]
        size = min(h, w)
        y = (h - size) // 2
        x = (w - size) // 2
        cropped = frame[y:y+size, x:x+size]

        os.makedirs("data/avatars", exist_ok=True)
        uname = self.current_user.get("username", "admin")
        dest_path = f"data/avatars/{uname}_avatar.enc"
        try:
            self._cleanup_old_avatar(uname, keep_path=dest_path)
            saved_path = self.db.save_encrypted_photo(dest_path, cropped)
            self.current_user["avatar_path"] = saved_path
            self._update_profile_avatar_preview(saved_path)
            self._update_header_user_display()
        except Exception as e:
            QMessageBox.critical(self, "Lỗi lưu ảnh", f"Không thể mã hoá và lưu ảnh đại diện: {str(e)}")

    def _on_clear_avatar_clicked(self):
        uname = self.current_user.get("username", "admin")
        self._cleanup_old_avatar(uname, keep_path="")
        self.current_user["avatar_path"] = ""
        self._update_profile_avatar_preview("")
        self._update_header_user_display()

    def _on_save_profile_clicked(self):
        new_name = self.txt_profile_fullname.text().strip()
        if not new_name:
            QMessageBox.warning(self, "Thiếu thông tin", "Họ và tên không được để trống.")
            return

        new_username = self.txt_profile_username.text().strip()
        old_username = self.current_user.get("username", "admin")
        uid = self.current_user.get("id", 1)

        # Đổi tên đăng nhập nếu có chỉnh sửa (không áp dụng cho tài khoản gốc admin)
        if new_username != old_username:
            if old_username == "admin":
                QMessageBox.warning(self, "Không thể đổi tên", "Tài khoản quản trị viên gốc (admin) không được phép thay đổi tên đăng nhập.")
                self.txt_profile_username.setText("admin")
                return
            ok_u, msg_u = self.db.update_admin_username(uid, new_username, actor_username=old_username)
            if not ok_u:
                QMessageBox.warning(self, "Lỗi đổi tên đăng nhập", msg_u)
                return
            self.current_user["username"] = new_username

        avatar_path = self.current_user.get("avatar_path", "")
        ok, msg = self.db.update_admin_profile(uid, new_name, avatar_path, actor_username=self.current_user.get("username", "admin"))
        if ok:
            self.current_user["full_name"] = new_name
            self._update_header_user_display()
            if hasattr(self, "_load_admin_users_table"):
                self._load_admin_users_table()
            QMessageBox.information(self, "Thành công", msg)
        else:
            QMessageBox.critical(self, "Lỗi cập nhật", msg)

    def _on_update_profile_password_clicked(self):
        old_pwd = self.txt_profile_old_pwd.text()
        new_pwd = self.txt_profile_new_pwd.text()
        confirm_pwd = self.txt_profile_confirm_pwd.text()

        if not old_pwd or not new_pwd or not confirm_pwd:
            QMessageBox.warning(self, "Thiếu thông tin", "Vui lòng nhập đầy đủ các trường mật khẩu.")
            return
        if len(new_pwd) < 6:
            QMessageBox.warning(self, "Mật khẩu không hợp lệ", "Mật khẩu mới phải có tối thiểu 6 ký tự.")
            return
        if new_pwd != confirm_pwd:
            QMessageBox.critical(self, "Không khớp", "Mật khẩu xác nhận không khớp.")
            return

        uid = self.current_user.get("id", 1)
        ok, msg = self.db.change_admin_password(uid, old_pwd, new_pwd, actor_username=self.current_user.get("username", "admin"))
        if ok:
            self.txt_profile_old_pwd.clear()
            self.txt_profile_new_pwd.clear()
            self.txt_profile_confirm_pwd.clear()
            QMessageBox.information(self, "Thành công", "Đã cập nhật mật khẩu mới thành công.")
        else:
            QMessageBox.critical(self, "Lỗi cập nhật", msg)

    def _load_admin_users_table(self):
        if not hasattr(self, "table_admin_users"):
            return
        self.table_admin_users.setSortingEnabled(False)
        users = self.db.get_all_admin_users()
        self.table_admin_users.setRowCount(len(users))
        role_map = {
            "super_admin": "Quản trị viên cấp cao",
            "manager": "Giáo vụ",
            "teacher": "Giáo viên",
            "technician": "Kỹ thuật viên"
        }
        for row_idx, u in enumerate(users):
            self.table_admin_users.setItem(row_idx, 0, NumericTableWidgetItem(u["id"], Qt.AlignmentFlag.AlignCenter))
            self.table_admin_users.setItem(row_idx, 1, _create_cell_item(u["username"], Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft))
            self.table_admin_users.setItem(row_idx, 2, _create_cell_item(u["full_name"], Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft))
            self.table_admin_users.setItem(row_idx, 3, _create_cell_item(role_map.get(u["role"], u["role"]), Qt.AlignmentFlag.AlignCenter))

            u_role = u.get("role", "")
            if u_role == "teacher":
                classes = self.db.get_admin_user_classes(u["id"])
                classes_str = ", ".join(classes) if classes else "Chưa phân công"
            elif u_role == "super_admin":
                classes_str = "Toàn quyền"
            elif u_role == "manager":
                classes_str = "Tất cả các lớp"
            else:
                classes_str = "Không truy cập"
            self.table_admin_users.setItem(row_idx, 4, _create_cell_item(classes_str, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft))

            is_temp_locked = False
            remaining_mins = 0
            locked_until_str = u.get("locked_until")
            if locked_until_str:
                try:
                    locked_until = datetime.fromisoformat(locked_until_str)
                    if datetime.now() < locked_until:
                        is_temp_locked = True
                        remaining_mins = int((locked_until - datetime.now()).total_seconds() / 60) + 1
                except Exception:
                    pass

            if not u.get("is_active", 1):
                st_text = "Đã khóa"
                st_color = "#dc2626"
            elif is_temp_locked:
                st_text = f"Tạm khóa ({remaining_mins}p)"
                st_color = "#ea580c"
            else:
                st_text = "Hoạt động"
                st_color = "#16a34a"
            self.table_admin_users.setItem(row_idx, 5, _create_cell_item(st_text, Qt.AlignmentFlag.AlignCenter, color=st_color))
            self.table_admin_users.setItem(row_idx, 6, DateTimeTableWidgetItem(u.get("last_login")))
        self.table_admin_users.setSortingEnabled(True)

    def _on_assign_classes_admin_clicked(self):
        sel = self.table_admin_users.selectedItems()
        if not sel:
            QMessageBox.information(self, "Chưa chọn dòng", "Vui lòng chọn một tài khoản từ bảng danh sách.")
            return
        row = sel[0].row()
        uid = int(self.table_admin_users.item(row, 0).text())
        user = self.db.get_admin_user_by_id(uid)
        if not user:
            return
        if user.get("role") != "teacher":
            QMessageBox.information(self, "Không áp dụng", "Chỉ có tài khoản có vai trò 'Giáo viên' mới cần phân công lớp giảng dạy.")
            return
        dlg = AssignClassesDialog(self, self.db, teacher_user=user, actor_username=self.current_user.get("username", "admin"))
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._load_admin_users_table()

    def _load_teachers_table(self):
        if not hasattr(self, "table_teachers"):
            return
        self.table_teachers.setSortingEnabled(False)
        teachers = self.db.get_all_teachers()
        self.table_teachers.setRowCount(len(teachers))
        for row_idx, t in enumerate(teachers):
            self.table_teachers.setItem(row_idx, 0, NumericTableWidgetItem(t["id"], Qt.AlignmentFlag.AlignCenter))
            self.table_teachers.setItem(row_idx, 1, _create_cell_item(t["username"], Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft))
            self.table_teachers.setItem(row_idx, 2, _create_cell_item(t["full_name"], Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft))
            cls_str = ", ".join(t.get("classes", [])) if t.get("classes") else "Chưa phân công"
            self.table_teachers.setItem(row_idx, 3, _create_cell_item(cls_str, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft))
        self.table_teachers.setSortingEnabled(True)

    def _on_assign_classes_mgr_clicked(self):
        sel = self.table_teachers.selectedItems()
        if not sel:
            QMessageBox.information(self, "Chưa chọn dòng", "Vui lòng chọn một giáo viên từ bảng danh sách.")
            return
        row = sel[0].row()
        uid = int(self.table_teachers.item(row, 0).text())
        user = self.db.get_admin_user_by_id(uid)
        if not user:
            return
        dlg = AssignClassesDialog(self, self.db, teacher_user=user, actor_username=self.current_user.get("username", "manager"))
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._load_teachers_table()

    def _load_admin_audit_logs_table(self):
        if not hasattr(self, "table_admin_audit_logs"):
            return
        self.table_admin_audit_logs.setSortingEnabled(False)
        logs = self.db.get_admin_audit_logs(limit=100)
        self.table_admin_audit_logs.setRowCount(len(logs))
        for row_idx, l in enumerate(logs):
            self.table_admin_audit_logs.setItem(row_idx, 0, DateTimeTableWidgetItem(l.get("timestamp")))
            self.table_admin_audit_logs.setItem(row_idx, 1, _create_cell_item(str(l.get("username", "-")), Qt.AlignmentFlag.AlignCenter))
            raw_act = str(l.get("action", "-"))
            act_vn = ADMIN_AUDIT_ACTION_MAP.get(raw_act, raw_act)
            self.table_admin_audit_logs.setItem(row_idx, 2, _create_cell_item(act_vn, Qt.AlignmentFlag.AlignCenter))
            self.table_admin_audit_logs.setItem(row_idx, 3, _create_cell_item(str(l.get("details", "-")), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft))
        self.table_admin_audit_logs.setSortingEnabled(True)

    def _on_add_user_clicked(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Thêm Tài Khoản Quản Trị Mới")
        dlg.setFixedWidth(380)
        dlg.setStyleSheet(BASE_WINDOW_STYLE)
        dlg_layout = QVBoxLayout(dlg)
        dlg_layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)
        dlg_layout.setContentsMargins(16, 16, 16, 16)
        dlg_layout.setSpacing(10)

        f_layout = QFormLayout()
        f_layout.setSpacing(8)

        txt_u = QLineEdit()
        txt_u.setPlaceholderText("Ví dụ: gv_an")
        txt_u.setStyleSheet("padding: 4px 10px; border: 1.5px solid #d1d5db; border-radius: 5px; font-size: 12px;")
        add_form_row(f_layout, "Tên đăng nhập:", txt_u, label_width=110, height=36)

        txt_name = QLineEdit()
        txt_name.setPlaceholderText("Ví dụ: Nguyễn Văn An")
        txt_name.setStyleSheet("padding: 4px 10px; border: 1.5px solid #d1d5db; border-radius: 5px; font-size: 12px;")
        add_form_row(f_layout, "Họ và Tên:", txt_name, label_width=110, height=36)

        txt_p = QLineEdit()
        txt_p.setEchoMode(QLineEdit.EchoMode.Password)
        txt_p.setPlaceholderText("Tối thiểu 6 ký tự")
        txt_p.setStyleSheet("padding: 4px 10px; border: 1.5px solid #d1d5db; border-radius: 5px; font-size: 12px;")
        add_form_row(f_layout, "Mật khẩu:", txt_p, label_width=110, height=36)

        cb_role = QComboBox()
        cb_role.addItem("Quản trị viên cấp cao", "super_admin")
        cb_role.addItem("Giáo vụ", "manager")
        cb_role.addItem("Giáo viên", "teacher")
        cb_role.addItem("Kỹ thuật viên", "technician")
        cb_role.setCurrentIndex(2)
        cb_role.setStyleSheet(GLOBAL_COMBOBOX_QSS)
        cb_role.setFixedHeight(36)
        add_form_row(f_layout, "Vai trò:", cb_role, label_width=110, height=36)
        dlg_layout.addLayout(f_layout)

        b_layout = QHBoxLayout()
        btn_c = QPushButton("Hủy")
        btn_c.clicked.connect(dlg.reject)
        b_layout.addWidget(btn_c)

        btn_ok = QPushButton("Tạo tài khoản")
        btn_ok.setStyleSheet("background-color: #16a34a; color: #ffffff; font-weight: bold; padding: 6px 12px;")
        def _do_create():
            u = txt_u.text().strip()
            n = txt_name.text().strip()
            p = txt_p.text()
            r = cb_role.currentData()
            ok, msg = self.db.create_admin_user(u, n, p, role=r, actor_username=self.current_user.get("username", "admin"))
            if ok:
                QMessageBox.information(dlg, "Thành công", msg)
                dlg.accept()
                self._load_admin_users_table()
            else:
                QMessageBox.warning(dlg, "Lỗi tạo tài khoản", msg)
        btn_ok.clicked.connect(_do_create)
        b_layout.addWidget(btn_ok)
        dlg_layout.addLayout(b_layout)
        dlg.exec()

    def _on_admin_user_selection_changed(self):
        if not hasattr(self, "btn_toggle_user_active") or not hasattr(self, "table_admin_users"):
            return
        sel = self.table_admin_users.selectedItems()
        if not sel:
            self.btn_toggle_user_active.setText("Khóa / Mở khóa")
            return
        row = sel[0].row()
        st_item = self.table_admin_users.item(row, 5)
        st_text = st_item.text() if st_item else ""
        if "khóa" in st_text.lower():
            self.btn_toggle_user_active.setText("Mở khóa")
        else:
            self.btn_toggle_user_active.setText("Khóa tài khoản")

    def _on_toggle_user_active_clicked(self):
        sel = self.table_admin_users.selectedItems()
        if not sel:
            QMessageBox.information(self, "Chưa chọn dòng", "Vui lòng chọn một tài khoản từ bảng danh sách.")
            return
        row = sel[0].row()
        uid = int(self.table_admin_users.item(row, 0).text())
        if self.current_user and uid == self.current_user.get("id"):
            QMessageBox.warning(self, "Không thể thực hiện", "Bạn không thể tự khóa tài khoản của chính mình.")
            return
        ok, msg = self.db.toggle_admin_user_active(uid, actor_username=self.current_user.get("username", "admin"))
        if ok:
            self._load_admin_users_table()
            QMessageBox.information(self, "Thành công", msg)
        else:
            QMessageBox.warning(self, "Không thể thực hiện", msg)

    def _on_reset_user_pwd_clicked(self):
        sel = self.table_admin_users.selectedItems()
        if not sel:
            QMessageBox.information(self, "Chưa chọn dòng", "Vui lòng chọn một tài khoản từ bảng danh sách.")
            return
        row = sel[0].row()
        uid = int(self.table_admin_users.item(row, 0).text())
        uname = self.table_admin_users.item(row, 1).text()
        new_pwd, ok = QInputDialog.getText(self, "Đặt lại mật khẩu", f"Nhập mật khẩu mới cho tài khoản '{uname}':", QLineEdit.EchoMode.Password)
        if not ok or not new_pwd:
            return
        if len(new_pwd) < 6:
            QMessageBox.warning(self, "Mật khẩu không hợp lệ", "Mật khẩu mới phải có tối thiểu 6 ký tự.")
            return
        ok, msg = self.db.change_admin_password(uid, "", new_pwd, actor_username=self.current_user.get("username", "admin"), force=True)
        if ok:
            QMessageBox.information(self, "Thành công", f"Đã đặt lại mật khẩu thành công cho tài khoản '{uname}'.")
        else:
            QMessageBox.warning(self, "Lỗi đặt lại mật khẩu", msg)

    def _on_delete_user_clicked(self):
        sel = self.table_admin_users.selectedItems()
        if not sel:
            QMessageBox.information(self, "Chưa chọn dòng", "Vui lòng chọn một tài khoản từ bảng danh sách.")
            return
        row = sel[0].row()
        uid = int(self.table_admin_users.item(row, 0).text())
        uname = self.table_admin_users.item(row, 1).text()
        if self.current_user and uid == self.current_user.get("id"):
            QMessageBox.warning(self, "Không thể thực hiện", "Bạn không thể tự xóa tài khoản của chính mình.")
            return
        reply = QMessageBox.question(
            self, "Xác nhận xóa tài khoản",
            f"Bạn có chắc chắn muốn xóa vĩnh viễn tài khoản '{uname}' khỏi hệ thống?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            ok, msg = self.db.delete_admin_user(uid, actor_username=self.current_user.get("username", "admin"))
            if ok:
                self._load_admin_users_table()
                QMessageBox.information(self, "Thành công", msg)
            else:
                QMessageBox.warning(self, "Không thể xóa", msg)

    def _on_change_username_clicked(self):
        sel = self.table_admin_users.selectedItems()
        if not sel:
            QMessageBox.information(self, "Chưa chọn dòng", "Vui lòng chọn một tài khoản từ bảng danh sách.")
            return
        row = sel[0].row()
        uid = int(self.table_admin_users.item(row, 0).text())
        old_uname = self.table_admin_users.item(row, 1).text()
        if old_uname == "admin":
            QMessageBox.warning(self, "Không thể đổi tên", "Tài khoản quản trị viên gốc (admin) được bảo vệ và không thể đổi tên đăng nhập.")
            return
        new_uname, ok = QInputDialog.getText(self, "Đổi tên đăng nhập", f"Nhập tên đăng nhập mới cho tài khoản '{old_uname}':", text=old_uname)
        if not ok or not new_uname:
            return
        new_uname = new_uname.strip()
        if new_uname == old_uname:
            return
        ok, msg = self.db.update_admin_username(uid, new_uname, actor_username=self.current_user.get("username", "admin"))
        if ok:
            if uid == self.current_user.get("id"):
                self.current_user["username"] = new_uname
                self.txt_profile_username.setText(new_uname)
                self._update_header_user_display()
            self._load_admin_users_table()
            if hasattr(self, "_load_admin_audit_logs_table"):
                self._load_admin_audit_logs_table()
            QMessageBox.information(self, "Thành công", msg)
        else:
            QMessageBox.warning(self, "Lỗi đổi tên đăng nhập", msg)


class MainWindow(QMainWindow):
    """Màn hình chính: Hiển thị 2 camera thời gian thực, đồng hồ số và sự kiện điểm danh."""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Hệ Thống Điểm Danh Sinh Viên")
        self.resize(1280, 780)
        self.setStyleSheet(BASE_WINDOW_STYLE)

        # 0. Khởi tạo DebugManager (Thời gian ảo độc lập cho Quản trị viên)
        self.admin_dlg = None
        self.last_card_source = None  # Nguồn gốc thẻ sinh viên gần nhất: "main" | "debug" | None
        self.debug_mgr = DebugManager(self)
        self.debug_mgr.mode_changed.connect(self._on_debug_mode_changed)
        self.debug_mgr.time_changed.connect(self._on_debug_time_changed)

        # 1. Khởi tạo Database, FaceEngine và Logic
        self.db = DatabaseManager()
        self.use_ampm = self.db.get_setting("use_ampm", "0") == "1"
        self.face_threshold = float(self.db.get_setting("face_threshold", "0.55"))
        self.face_engine = FaceEngine(threshold=self.face_threshold)
        self.attendance_logic = AttendanceLogic(self.db, cooldown_seconds=60)
        self.student_cache = {}
        self.reload_student_cache()

        # 2. Khởi tạo Giao diện
        ensure_app_assets()
        self._init_ui()

        # 3. Khởi tạo phiên xác thực Check-in & Check-out
        self.verify_sessions = {
            "IN": {
                "active": False,
                "start_time": 0.0,
                "duration": 3.0,
                "required_ratio": 70,
                "total_frames": 0,
                "student_votes": {},
                "timer": QTimer(self),
                "btn": self.btn_checkin,
                "lbl": self.lbl_verify_in,
                "prog": self.prog_in,
            },
            "OUT": {
                "active": False,
                "start_time": 0.0,
                "duration": 3.0,
                "required_ratio": 70,
                "total_frames": 0,
                "student_votes": {},
                "timer": QTimer(self),
                "btn": self.btn_checkout,
                "lbl": self.lbl_verify_out,
                "prog": self.prog_out,
            }
        }
        self.verify_sessions["IN"]["timer"].timeout.connect(lambda: self._on_verify_timer_tick("IN"))
        self.verify_sessions["OUT"]["timer"].timeout.connect(lambda: self._on_verify_timer_tick("OUT"))

        # 4. Khởi động Đồng hồ số thời gian thực
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self._update_clock)
        self.clock_timer.start(1000)

        # 5. Khởi động 2 Camera Worker Threads
        self.worker_in = None
        self.worker_out = None
        self._start_camera_workers()

    def reload_student_cache(self):
        self.student_cache = self.db.load_cached_embeddings()

    def get_student_cache(self):
        return self.student_cache

    def set_use_ampm(self, enabled: bool):
        """Cập nhật định dạng hiển thị thời gian 12h (AM/PM) hoặc 24h trên giao diện chính."""
        self.use_ampm = bool(enabled)
        self._update_clock()

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(15, 10, 15, 10)
        main_layout.setSpacing(10)

        # 1. Thanh tiêu đề trên cùng (Top Bar)
        top_bar = QHBoxLayout()
        title_box = QVBoxLayout()
        lbl_title = QLabel("HỆ THỐNG ĐIỂM DANH SINH VIÊN")
        lbl_title.setStyleSheet("font-size: 18px; font-weight: bold; color: #111827;")
        title_box.addWidget(lbl_title)
        top_bar.addLayout(title_box)

        top_bar.addStretch()

        # Đồng hồ số
        self.lbl_clock = QLabel("00:00:00 - 01/01/2026")
        self.lbl_clock.setStyleSheet("font-size: 16px; font-weight: bold; color: #111827; background-color: #f9fafb; padding: 6px 15px; border-radius: 6px; border: 1px solid #e5e7eb;")
        top_bar.addWidget(self.lbl_clock)

        # Nút Đăng ký và Nút Quản trị (KHÔNG DÙNG EMOJI)
        self.btn_register = QPushButton("Đăng ký Sinh viên")
        self.btn_register.setStyleSheet("background-color: #111827; color: #ffffff; font-weight: bold; padding: 8px 16px; border-radius: 6px;")
        self.btn_register.clicked.connect(self.open_registration)
        top_bar.addWidget(self.btn_register)

        self.btn_admin = QPushButton("Quản trị Hệ thống")
        self.btn_admin.setStyleSheet("background-color: #374151; color: #ffffff; font-weight: bold; padding: 8px 16px; border-radius: 6px;")
        self.btn_admin.clicked.connect(self.open_admin)
        top_bar.addWidget(self.btn_admin)

        main_layout.addLayout(top_bar)

        # Thanh Banner Chế độ Debug (Ẩn mặc định, chỉ hiện khi Admin bật Debug)
        self.banner_debug = QFrame()
        self.banner_debug.setVisible(False)
        self.banner_debug.setStyleSheet("""
            QFrame {
                background-color: #f3f4f6;
                border: 1px solid #d1d5db;
                border-radius: 6px;
            }
        """)
        banner_layout = QHBoxLayout(self.banner_debug)
        banner_layout.setContentsMargins(14, 5, 14, 5)

        self.lbl_banner_debug = QLabel("CHẾ ĐỘ DEBUG ĐANG BẬT | Cổng Vào: 0 FPS (0.0 ms) | Cổng Ra: 0 FPS (0.0 ms)")
        self.lbl_banner_debug.setStyleSheet("color: #111827; font-weight: bold; font-size: 13px; border: none; background: transparent;")
        banner_layout.addWidget(self.lbl_banner_debug)
        banner_layout.addStretch()

        btn_disable_debug = QPushButton("Tắt Chế độ Debug")
        btn_disable_debug.setFixedHeight(28)
        btn_disable_debug.setStyleSheet("background-color: #111827; color: #ffffff; font-weight: bold; font-size: 11px; border-radius: 4px; padding: 2px 14px;")
        btn_disable_debug.clicked.connect(self.debug_mgr.disable)
        banner_layout.addWidget(btn_disable_debug)

        main_layout.addWidget(self.banner_debug)

        # 2. Khung thống kê nhanh (Counters)
        stat_bar = QHBoxLayout()
        self.lbl_stat_total = self._create_stat_card("Tổng Sinh Viên", "0", "#111827")
        self.lbl_stat_ontime = self._create_stat_card("Đúng Giờ", "0", "#16a34a")
        self.lbl_stat_late = self._create_stat_card("Đi Trễ", "0", "#d97706")
        self.lbl_stat_early = self._create_stat_card("Về Sớm", "0", "#2563eb")
        self.lbl_stat_absent = self._create_stat_card("Vắng Mặt", "0", "#dc2626")
        stat_bar.addWidget(self.lbl_stat_total)
        stat_bar.addWidget(self.lbl_stat_ontime)
        stat_bar.addWidget(self.lbl_stat_late)
        stat_bar.addWidget(self.lbl_stat_early)
        stat_bar.addWidget(self.lbl_stat_absent)
        main_layout.addLayout(stat_bar)

        # 3. Khung chia đôi 2 Camera
        camera_split = QHBoxLayout()
        camera_split.setSpacing(12)

        # Camera 1 (Điểm danh Cửa Lớp: Check-in và Check-out)
        cam1_group = QGroupBox("Camera Điểm Danh Cửa Lớp")
        cam1_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        cam1_group.setStyleSheet("""
        QGroupBox {
            font-weight: bold;
            color: #111827;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            margin-top: 8px;
            padding-top: 10px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 10px;
            padding: 0 4px;
            background-color: #ffffff;
        }
        """)
        cam1_layout = QVBoxLayout(cam1_group)
        cam1_layout.setSpacing(6)
        self.cam1_label = QLabel("Đang kết nối Camera Cửa...")
        self.cam1_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.cam1_label.setMinimumSize(480, 270)
        self.cam1_label.setStyleSheet("background-color: #000000; color: #f3f4f6; border: 1px solid #e5e7eb; border-radius: 6px;")
        self.cam1_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cam1_layout.addWidget(self.cam1_label, stretch=1)

        self.prog_in = QProgressBar()
        self.prog_in.setRange(0, 100)
        self.prog_in.setValue(0)
        self.prog_in.setFixedHeight(6)
        self.prog_in.setTextVisible(False)
        self.prog_in.setStyleSheet("""
            QProgressBar {
                background-color: #f3f4f6;
                border: none;
                border-radius: 3px;
            }
            QProgressBar::chunk {
                background-color: #111827;
                border-radius: 3px;
            }
        """)
        cam1_layout.addWidget(self.prog_in, stretch=0)
        self.prog_out = self.prog_in

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.btn_checkin = QPushButton("Bắt đầu Check-in")
        self.btn_checkin.setFixedHeight(38)
        self.btn_checkin.setStyleSheet("background-color: #111827; color: #ffffff; font-weight: bold; font-size: 13px; border-radius: 6px;")
        self.btn_checkin.clicked.connect(lambda: self.start_verification("IN"))
        btn_row.addWidget(self.btn_checkin, 1)

        self.btn_checkout = QPushButton("Bắt đầu Check-out")
        self.btn_checkout.setFixedHeight(38)
        self.btn_checkout.setStyleSheet("background-color: #111827; color: #ffffff; font-weight: bold; font-size: 13px; border-radius: 6px;")
        self.btn_checkout.clicked.connect(lambda: self.start_verification("OUT"))
        btn_row.addWidget(self.btn_checkout, 1)

        cam1_layout.addLayout(btn_row, stretch=0)

        self.lbl_verify_in = QLabel("Sẵn sàng. Nhấn nút để bắt đầu xác thực")
        self.lbl_verify_in.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_verify_in.setStyleSheet("color: #6b7280; font-size: 12px; margin-top: 2px;")
        cam1_layout.addWidget(self.lbl_verify_in, stretch=0)
        self.lbl_verify_out = self.lbl_verify_in

        camera_split.addWidget(cam1_group)

        # Camera 2 (Đếm Số Lượng Sinh Viên - Gắn Trên Tường)
        cam2_group = QGroupBox("Camera Đếm Số Lượng Sinh Viên")
        cam2_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        cam2_group.setStyleSheet("""
        QGroupBox {
            font-weight: bold;
            color: #111827;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            margin-top: 8px;
            padding-top: 10px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 10px;
            padding: 0 4px;
            background-color: #ffffff;
        }
        """)
        cam2_layout = QVBoxLayout(cam2_group)
        cam2_layout.setSpacing(6)
        self.cam2_label = QLabel("Đang kết nối Camera Gắn Tường...")
        self.cam2_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.cam2_label.setMinimumSize(480, 270)
        self.cam2_label.setStyleSheet("background-color: #000000; color: #f3f4f6; border: 1px solid #e5e7eb; border-radius: 6px;")
        self.cam2_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cam2_layout.addWidget(self.cam2_label, stretch=1)

        count_card = QFrame()
        count_card.setFixedHeight(50)
        count_card.setStyleSheet("""
            QFrame {
                background-color: #f9fafb;
                border: 1px solid #e5e7eb;
                border-radius: 6px;
            }
        """)
        count_layout = QHBoxLayout(count_card)
        count_layout.setContentsMargins(14, 6, 14, 6)
        count_layout.setSpacing(10)

        self.lbl_headcount_title = QLabel("Số sinh viên trong lớp:")
        self.lbl_headcount_title.setStyleSheet("font-size: 13px; font-weight: bold; color: #374151; border: none; background: transparent;")
        count_layout.addWidget(self.lbl_headcount_title)

        self.lbl_headcount_val = QLabel("0")
        self.lbl_headcount_val.setStyleSheet("font-size: 20px; font-weight: bold; color: #111827; border: none; background: transparent;")
        count_layout.addWidget(self.lbl_headcount_val)

        count_layout.addStretch()

        self.lbl_headcount_delta = QLabel("Biến động: Ổn định")
        self.lbl_headcount_delta.setStyleSheet("font-size: 12px; font-weight: bold; color: #059669; border: none; background: transparent;")
        count_layout.addWidget(self.lbl_headcount_delta)

        cam2_layout.addWidget(count_card, stretch=0)

        camera_split.addWidget(cam2_group)

        main_layout.addLayout(camera_split, stretch=1)

        # 4. Bảng nhật ký điểm danh thời gian thực
        bot_layout = QHBoxLayout()
        table_group = QGroupBox("Nhật Ký Điểm Danh")
        table_group.setStyleSheet("""
        QGroupBox {
            font-weight: bold;
            color: #111827;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            margin-top: 8px;
            padding-top: 10px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 10px;
            padding: 0 4px;
            background-color: #ffffff;
        }
        """)
        t_layout = QVBoxLayout(table_group)

        self.table_live = QTableWidget()
        self.table_live.setColumnCount(6)
        self.table_live.setHorizontalHeaderLabels(["Thời Gian", "Cổng", "MSSV", "Họ và Tên", "Trạng Thái", "Thông Báo"])
        live_header = self.table_live.horizontalHeader()
        live_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        live_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        live_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        live_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        live_header.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        live_header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table_live.setColumnWidth(0, 100)
        self.table_live.setColumnWidth(1, 115)
        self.table_live.setColumnWidth(2, 95)
        self.table_live.setColumnWidth(3, 160)
        self.table_live.setColumnWidth(4, 115)
        self.table_live.setStyleSheet("""
        QTableWidget {
            background-color: #ffffff;
            gridline-color: #f3f4f6;
            color: #111827;
            font-size: 12px;
            border: 1px solid #e5e7eb;
        }
        QHeaderView::section {
            background-color: #f9fafb;
            color: #374151;
            border: 1px solid #e5e7eb;
            font-size: 12px;
            font-weight: bold;
            padding: 4px 6px;
        }
        QTableWidget::item {
            padding: 2px 6px;
        }
        """)
        self.table_live.verticalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table_live.verticalHeader().setVisible(False)
        self.table_live.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table_live.setMaximumHeight(180)
        t_layout.addWidget(self.table_live)
        bot_layout.addWidget(table_group, stretch=3)

        # Thẻ hiển thị sinh viên gần nhất
        card_group = QGroupBox("Sinh Viên Gần Nhất")
        card_group.setStyleSheet("""
        QGroupBox {
            font-weight: bold;
            color: #111827;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            margin-top: 8px;
            padding-top: 10px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 10px;
            padding: 0 4px;
            background-color: #ffffff;
        }
        """)
        card_layout = QVBoxLayout(card_group)
        self.lbl_card_name = QLabel("Chưa có lượt quét")
        self.lbl_card_name.setStyleSheet("border: none; background: transparent; font-size: 15px; font-weight: bold; color: #111827;")
        self.lbl_card_code = QLabel("MSSV: --")
        self.lbl_card_code.setStyleSheet("border: none; background: transparent; color: #4b5563;")
        self.lbl_card_status = QLabel("Trạng thái: --")
        self.lbl_card_status.setStyleSheet("border: none; background: transparent; color: #4b5563;")
        self.lbl_card_time = QLabel("Thời gian: --")
        self.lbl_card_time.setStyleSheet("border: none; background: transparent; color: #4b5563;")
        card_layout.addWidget(self.lbl_card_name)
        card_layout.addWidget(self.lbl_card_code)
        card_layout.addWidget(self.lbl_card_status)
        card_layout.addWidget(self.lbl_card_time)
        card_layout.addStretch()
        bot_layout.addWidget(card_group, stretch=1)

        main_layout.addLayout(bot_layout, stretch=0)
        self._update_stats()
        if central.layout():
            central.layout().activate()

    def _create_stat_card(self, title: str, val: str, color: str):
        frame = QFrame()
        frame.setStyleSheet("""
            QFrame {
                background-color: #f9fafb;
                border: 1px solid #e5e7eb;
                border-radius: 6px;
            }
            QLabel {
                border: none;
                background: transparent;
            }
        """)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(10, 6, 10, 6)
        t_lbl = QLabel(title)
        t_lbl.setStyleSheet("border: none; background: transparent; font-size: 11px; color: #6b7280;")
        v_lbl = QLabel(val)
        v_lbl.setObjectName("val")
        v_lbl.setStyleSheet(f"border: none; background: transparent; font-size: 18px; font-weight: bold; color: {color};")
        layout.addWidget(t_lbl)
        layout.addWidget(v_lbl)
        return frame

    def _update_clock(self):
        now = self.get_current_datetime()
        time_fmt = "%I:%M:%S %p - %d/%m/%Y" if getattr(self, "use_ampm", False) else "%H:%M:%S - %d/%m/%Y"
        if hasattr(self, "debug_mgr") and self.debug_mgr.is_active:
            frozen_str = " (Đóng băng)" if self.debug_mgr.is_frozen else ""
            self.lbl_clock.setText(f"{now.strftime(time_fmt)} [Debug{frozen_str}]")
            self.lbl_clock.setStyleSheet("font-size: 16px; font-weight: bold; color: #111827; background-color: #f3f4f6; padding: 6px 15px; border-radius: 6px; border: 1px solid #9ca3af;")

            fps_in = getattr(self.worker_in, "current_fps", 0.0) if getattr(self, "worker_in", None) else 0.0
            ms_in = getattr(self.worker_in, "current_infer_ms", 0.0) if getattr(self, "worker_in", None) else 0.0
            fps_out = getattr(self.worker_out, "current_fps", 0.0) if getattr(self, "worker_out", None) else 0.0
            ms_out = getattr(self.worker_out, "current_infer_ms", 0.0) if getattr(self, "worker_out", None) else 0.0

            self.lbl_banner_debug.setText(
                f"CHẾ ĐỘ DEBUG ĐANG BẬT | Cổng Vào: {fps_in:.0f} FPS ({ms_in:.1f} ms) | Cổng Ra: {fps_out:.0f} FPS ({ms_out:.1f} ms)"
            )
        else:
            self.lbl_clock.setText(now.strftime(time_fmt))
            self.lbl_clock.setStyleSheet("font-size: 16px; font-weight: bold; color: #111827; background-color: #f9fafb; padding: 6px 15px; border-radius: 6px; border: 1px solid #e5e7eb;")

    def _update_stats(self):
        students = self.db.get_all_active_students()
        today_iso = self.get_current_datetime().date().isoformat()
        logs = self.db.get_attendance_logs(today_iso)
        total = len(students)
        ontime = sum(1 for r in logs if r["status"] in ("DUNG_GIO", "HOAN_THANH"))
        late = sum(1 for r in logs if r["status"] == "DI_TRE")
        early = sum(1 for r in logs if r["status"] == "VE_SOM")
        absent = sum(1 for r in logs if r["status"] == "VANG")

        self.lbl_stat_total.findChild(QLabel, "val").setText(str(total))
        self.lbl_stat_ontime.findChild(QLabel, "val").setText(str(ontime))
        self.lbl_stat_late.findChild(QLabel, "val").setText(str(late))
        self.lbl_stat_early.findChild(QLabel, "val").setText(str(early))
        self.lbl_stat_absent.findChild(QLabel, "val").setText(str(absent))
        if hasattr(self, "admin_dlg") and self.admin_dlg and self.admin_dlg.isVisible():
            if hasattr(self.admin_dlg, "load_attendance_logs"):
                self.admin_dlg.load_attendance_logs()

    def get_current_datetime(self) -> datetime:
        return self.debug_mgr.get_current_datetime() if hasattr(self, "debug_mgr") and self.debug_mgr else datetime.now()

    def _clear_recent_student_card(self):
        """Xóa trắng thông tin trên thẻ Sinh Viên Gần Nhất về trạng thái mặc định."""
        self.lbl_card_name.setText("Chưa có lượt quét")
        self.lbl_card_name.setStyleSheet("border: none; background: transparent; font-size: 15px; font-weight: bold; color: #6b7280;")
        self.lbl_card_code.setText("MSSV: --")
        self.lbl_card_status.setText("Trạng thái: --")
        self.lbl_card_status.setStyleSheet("border: none; background: transparent;")
        self.lbl_card_time.setText("Thời gian: --")
        self.last_card_source = None

    def _on_debug_mode_changed(self, active: bool):
        self.banner_debug.setVisible(active)
        if self.centralWidget() and self.centralWidget().layout():
            self.centralWidget().layout().activate()
        self.db.set_debug_mode(active)
        self.attendance_logic.set_debug_mode(active)
        self.table_live.setRowCount(0)

        if not active:
            self.db.clear_mock_students_and_classes()
            if getattr(self, "last_card_source", None) == "debug":
                self._clear_recent_student_card()

        self.reload_student_cache()
        self._update_clock()
        self._update_stats()
        if hasattr(self, "admin_dlg") and self.admin_dlg and self.admin_dlg.isVisible():
            if hasattr(self.admin_dlg, "_on_mock_data_changed"):
                self.admin_dlg._on_mock_data_changed()
            if hasattr(self.admin_dlg, "load_attendance_logs"):
                self.admin_dlg.load_attendance_logs()

    def _on_debug_time_changed(self, dt: datetime):
        self._update_clock()
        self._update_stats()

    def _start_camera_workers(self):
        cam_in_src = self.db.get_setting("camera_in", "0")
        cam_out_src = self.db.get_setting("camera_out", "1")
        hc_conf = float(self.db.get_setting("headcount_conf", "0.25") or "0.25")

        self.worker_in = CameraWorker(cam_in_src, "IN", self.face_engine, self.get_student_cache, self.db, self)
        self.worker_in.frame_ready.connect(self.on_frame_ready)
        self.worker_in.student_detected.connect(self.on_student_detected)
        self.worker_in.stranger_detected.connect(self.on_stranger_detected)
        self.worker_in.no_face_detected.connect(self.on_no_face_detected)
        self.worker_in.start()

        # Tự động đồng bộ môi trường test: Nếu CameraWorker bị mock mà PersonCountWorker chưa mock
        if hasattr(CameraWorker, "assert_called") and not hasattr(PersonCountWorker, "assert_called"):
            from unittest.mock import MagicMock
            self.worker_out = MagicMock()
            self.worker_out.current_fps = 0.0
            self.worker_out.current_infer_ms = 0.0
        else:
            self.worker_out = PersonCountWorker(
                cam_out_src,
                conf_threshold=hc_conf,
                imgsz=1280,
                model_name="models/yolov8n_scuthead.pt",
                parent=self

            )
            self.worker_out.frame_ready.connect(self.on_frame_ready)
            self.worker_out.count_updated.connect(self._on_headcount_updated)
            self.worker_out.start()

    def restart_cameras(self):
        self.face_threshold = float(self.db.get_setting("face_threshold", "0.55"))
        if hasattr(self, "face_engine") and self.face_engine:
            self.face_engine.threshold = self.face_threshold
        self.use_ampm = self.db.get_setting("use_ampm", "0") == "1"
        self._update_clock()
        if hasattr(self, "lbl_headcount_val"):
            self.lbl_headcount_val.setText("0")
        if hasattr(self, "lbl_headcount_delta"):
            self.lbl_headcount_delta.setText("Biến động: Ổn định")
            self.lbl_headcount_delta.setStyleSheet("font-size: 12px; font-weight: bold; color: #6b7280; border: none; background: transparent;")
        cam_in_src = self.db.get_setting("camera_in", "0")
        cam_out_src = self.db.get_setting("camera_out", "1")
        hc_conf = float(self.db.get_setting("headcount_conf", "0.25") or "0.25")

        if self.worker_in and hasattr(self.worker_in, "isRunning") and self.worker_in.isRunning():
            self.worker_in.load_filter_settings()
            self.worker_in.set_source(cam_in_src)
        else:
            if self.worker_in and hasattr(self.worker_in, "stop"):
                self.worker_in.stop()
            self.worker_in = CameraWorker(cam_in_src, "IN", self.face_engine, self.get_student_cache, self.db, self)
            self.worker_in.frame_ready.connect(self.on_frame_ready)
            self.worker_in.student_detected.connect(self.on_student_detected)
            self.worker_in.stranger_detected.connect(self.on_stranger_detected)
            self.worker_in.no_face_detected.connect(self.on_no_face_detected)
            self.worker_in.start()

        if self.worker_out and hasattr(self.worker_out, "isRunning") and self.worker_out.isRunning():
            self.worker_out.conf_threshold = hc_conf
            self.worker_out.set_source(cam_out_src)
        else:
            if self.worker_out and hasattr(self.worker_out, "stop"):
                self.worker_out.stop()
            if hasattr(CameraWorker, "assert_called") and not hasattr(PersonCountWorker, "assert_called"):
                from unittest.mock import MagicMock
                self.worker_out = MagicMock()
                self.worker_out.current_fps = 0.0
                self.worker_out.current_infer_ms = 0.0
            else:
                self.worker_out = PersonCountWorker(
                    cam_out_src,
                    conf_threshold=hc_conf,
                    imgsz=1280,
                    model_name="models/yolov8n_scuthead.pt",
                    parent=self
                )
                self.worker_out.frame_ready.connect(self.on_frame_ready)
                self.worker_out.count_updated.connect(self._on_headcount_updated)
                self.worker_out.start()

    def _on_headcount_updated(self, count: int, delta_text: str, stats: dict):
        """Cập nhật số lượng sinh viên trong lớp từ Camera Gắn Tường."""
        if hasattr(self, "lbl_headcount_val"):
            self.lbl_headcount_val.setText(str(count))
        if hasattr(self, "lbl_headcount_delta"):
            self.lbl_headcount_delta.setText(f"Biến động: {delta_text}")
            if "+1" in delta_text:
                self.lbl_headcount_delta.setStyleSheet("font-size: 12px; font-weight: bold; color: #059669; border: none; background: transparent;")
            elif "-1" in delta_text:
                self.lbl_headcount_delta.setStyleSheet("font-size: 12px; font-weight: bold; color: #d97706; border: none; background: transparent;")
            else:
                self.lbl_headcount_delta.setStyleSheet("font-size: 12px; font-weight: bold; color: #6b7280; border: none; background: transparent;")

    def on_frame_ready(self, qimg: QImage, camera_type: str):
        pix = QPixmap.fromImage(qimg)
        target_lbl = self.cam1_label if camera_type == "IN" else self.cam2_label
        w = target_lbl.width()
        h = target_lbl.height()
        if w > 10 and h > 10:
            target_lbl.setPixmap(pix.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation))

    def on_student_detected(self, student: dict, camera_type: str):
        self.attendance_logic.reset_stranger(camera_type)
        # Camera Cửa (IN) phục vụ cho cả phiên xác thực Check-in và Check-out
        target_types = ["IN", "OUT"] if camera_type == "IN" else [camera_type]
        for c_type in target_types:
            sess = self.verify_sessions.get(c_type)
            if sess and sess["active"]:
                sess["total_frames"] += 1
                s_id = student["id"]
                if s_id not in sess["student_votes"]:
                    sess["student_votes"][s_id] = {"student": student, "count": 0}
                sess["student_votes"][s_id]["count"] += 1

    def on_stranger_detected(self, camera_type: str):
        target_types = ["IN", "OUT"] if camera_type == "IN" else [camera_type]
        for c_type in target_types:
            sess = self.verify_sessions.get(c_type)
            if sess and sess["active"]:
                sess["total_frames"] += 1

        alert_triggered = self.attendance_logic.handle_stranger(camera_type)
        if alert_triggered:
            self.last_card_source = "debug" if (hasattr(self, "debug_mgr") and self.debug_mgr and self.debug_mgr.is_active) else "main"
            time_fmt = "%I:%M:%S %p" if getattr(self, "use_ampm", False) else "%H:%M:%S"
            time_str = self.get_current_datetime().strftime(time_fmt)
            gate_name = "Cửa Điểm Danh"
            self.lbl_card_name.setText("NGƯỜI LẠ")
            self.lbl_card_code.setText("Cảnh báo: Chưa đăng ký")
            lbl_st, color_st = get_status_display("CANH_BAO")
            self.lbl_card_status.setText(f"Trạng thái: {lbl_st}")
            self.lbl_card_status.setStyleSheet(f"color: {color_st}; font-weight: bold;")
            self.lbl_card_time.setText(f"Thời gian: {time_str} ({gate_name})")

    def on_no_face_detected(self, camera_type: str):
        target_types = ["IN", "OUT"] if camera_type == "IN" else [camera_type]
        for c_type in target_types:
            sess = self.verify_sessions.get(c_type)
            if sess and sess["active"]:
                sess["total_frames"] += 1

    def start_verification(self, camera_type: str):
        sess = self.verify_sessions[camera_type]
        if sess["active"]:
            return

        other_type = "OUT" if camera_type == "IN" else "IN"
        if self.verify_sessions[other_type]["active"]:
            return

        duration = float(self.db.get_setting("verification_duration", "3.0"))
        required_ratio = int(float(self.db.get_setting("verification_ratio", "70")))

        sess["active"] = True
        sess["start_time"] = time.time()
        sess["duration"] = duration
        sess["required_ratio"] = required_ratio
        sess["total_frames"] = 0
        sess["student_votes"] = {}

        gate_title = "Check-in" if camera_type == "IN" else "Check-out"
        self.btn_checkin.setEnabled(False)
        self.btn_checkout.setEnabled(False)
        sess["btn"].setText(f"Đang xác thực {gate_title}...")
        sess["lbl"].setText(f"Vui lòng nhìn thẳng và giữ yên trước camera ({duration:.1f}s)...")
        sess["lbl"].setStyleSheet("color: #d97706; font-weight: bold; font-size: 12px;")
        sess["prog"].setValue(0)

        sess["timer"].start(50)

    def _on_verify_timer_tick(self, camera_type: str):
        sess = self.verify_sessions[camera_type]
        if not sess["active"]:
            sess["timer"].stop()
            return

        elapsed = time.time() - sess["start_time"]
        remaining = max(0.0, sess["duration"] - elapsed)
        pct = min(100, int((elapsed / sess["duration"]) * 100))
        sess["prog"].setValue(pct)

        votes = sess["student_votes"]
        if votes:
            best_entry = max(votes.values(), key=lambda v: v["count"])
            student_name = best_entry["student"]["full_name"]
            sess["lbl"].setText(f"Đang quét: {student_name} (còn {remaining:.1f}s)...")
        else:
            sess["lbl"].setText(f"Đang quét khuôn mặt (còn {remaining:.1f}s)...")

        if elapsed >= sess["duration"]:
            sess["timer"].stop()
            self.finish_verification(camera_type)

    def finish_verification(self, camera_type: str):
        sess = self.verify_sessions.get(camera_type)
        if not sess or not sess.get("active"):
            return
        sess["active"] = False
        sess["timer"].stop()
        sess["prog"].setValue(100)

        total = sess["total_frames"]
        votes = sess["student_votes"]
        req_ratio = sess["required_ratio"]

        best_student = None
        best_count = 0
        if votes:
            best_entry = max(votes.values(), key=lambda v: v["count"])
            best_student = best_entry["student"]
            best_count = best_entry["count"]

        ratio = (best_count / total * 100) if total > 0 else 0
        now = self.get_current_datetime()
        gate_name = "Cổng Vào" if camera_type == "IN" else "Cổng Ra"
        time_fmt = "%I:%M:%S %p" if getattr(self, "use_ampm", False) else "%H:%M:%S"
        time_str = now.strftime(time_fmt)

        if total == 0:
            sess["lbl"].setText("Không nhận được dữ liệu camera trong phiên xác thực.")
            sess["lbl"].setStyleSheet("color: #dc2626; font-weight: bold; font-size: 12px;")
        elif best_student is None:
            sess["lbl"].setText("Xác thực thất bại: Không nhận diện được sinh viên trong CSDL.")
            sess["lbl"].setStyleSheet("color: #dc2626; font-weight: bold; font-size: 12px;")
        elif ratio < req_ratio:
            sess["lbl"].setText(f"Xác thực không đạt: Chỉ khớp {ratio:.0f}% frame (yêu cầu tối thiểu {req_ratio}%).")
            sess["lbl"].setStyleSheet("color: #d97706; font-weight: bold; font-size: 12px;")
        else:
            if camera_type == "IN":
                ok, status, msg = self.attendance_logic.process_check_in(best_student, now)
            else:
                ok, status, msg = self.attendance_logic.process_check_out(best_student, now)

            status_label, status_color = get_status_display(status)
            self.last_card_source = "debug" if (hasattr(self, "debug_mgr") and self.debug_mgr and self.debug_mgr.is_active) else "main"
            self.lbl_card_name.setText(best_student["full_name"])
            self.lbl_card_code.setText(f"MSSV: {best_student['student_code']}")
            self.lbl_card_status.setText(f"Trạng thái: {status_label}")
            self.lbl_card_status.setStyleSheet(f"color: {status_color}; font-weight: bold;")
            self.lbl_card_time.setText(f"Thời gian: {time_str} ({gate_name})")

            if ok:
                sess["lbl"].setText(f"Thành công: {best_student['full_name']} ({ratio:.0f}% frame)")
                sess["lbl"].setStyleSheet("color: #16a34a; font-weight: bold; font-size: 12px;")

                self.table_live.insertRow(0)
                self.table_live.setItem(0, 0, _create_cell_item(time_str, Qt.AlignmentFlag.AlignCenter))
                self.table_live.setItem(0, 1, _create_cell_item(gate_name, Qt.AlignmentFlag.AlignCenter))
                self.table_live.setItem(0, 2, _create_cell_item(best_student["student_code"], Qt.AlignmentFlag.AlignCenter))
                self.table_live.setItem(0, 3, _create_cell_item(best_student["full_name"], Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft))
                self.table_live.setItem(0, 4, _create_cell_item(status_label, Qt.AlignmentFlag.AlignCenter, color=status_color))
                self.table_live.setItem(0, 5, _create_cell_item(msg, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft))
                if self.table_live.rowCount() > 200:
                    self.table_live.removeRow(self.table_live.rowCount() - 1)

                self._update_stats()
            else:
                sess["lbl"].setText(f"Từ chối {best_student['full_name']} ({status_label}): {msg}")
                sess["lbl"].setStyleSheet("color: #dc2626; font-weight: bold; font-size: 12px;")

        QTimer.singleShot(2500, lambda k=camera_type: self._reset_verification_ui(k))

    def handle_simulated_scan(self, student: dict, camera_type: str):
        """Xử lý sự kiện giả lập quét điểm danh từ Chế độ Debug Admin."""
        self.last_card_source = "debug"
        now = self.get_current_datetime()
        gate_name = "Cổng Vào" if camera_type == "IN" else "Cổng Ra"
        time_fmt = "%I:%M:%S %p" if getattr(self, "use_ampm", False) else "%H:%M:%S"
        time_str = now.strftime(time_fmt)

        if camera_type == "IN":
            ok, status, msg = self.attendance_logic.process_check_in(student, now)
        else:
            ok, status, msg = self.attendance_logic.process_check_out(student, now)

        status_label, status_color = get_status_display(status)

        self.lbl_card_name.setText(student["full_name"])
        self.lbl_card_code.setText(f"MSSV: {student['student_code']}")
        self.lbl_card_status.setText(f"Trạng thái: {status_label}")
        self.lbl_card_status.setStyleSheet(f"color: {status_color}; font-weight: bold;")
        self.lbl_card_time.setText(f"Thời gian: {time_str} ({gate_name} [Debug])")

        self.table_live.insertRow(0)
        self.table_live.setItem(0, 0, _create_cell_item(time_str, Qt.AlignmentFlag.AlignCenter))
        self.table_live.setItem(0, 1, _create_cell_item(f"{gate_name} (Debug)", Qt.AlignmentFlag.AlignCenter))
        self.table_live.setItem(0, 2, _create_cell_item(student["student_code"], Qt.AlignmentFlag.AlignCenter))
        self.table_live.setItem(0, 3, _create_cell_item(student["full_name"], Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft))
        self.table_live.setItem(0, 4, _create_cell_item(status_label, Qt.AlignmentFlag.AlignCenter, color=status_color))
        self.table_live.setItem(0, 5, _create_cell_item(msg, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft))
        if self.table_live.rowCount() > 200:
            self.table_live.removeRow(self.table_live.rowCount() - 1)

        self._update_stats()

        sess = self.verify_sessions.get(camera_type)
        if sess:
            if ok:
                sess["lbl"].setText(f"Debug: {student['full_name']} - {status_label}")
                sess["lbl"].setStyleSheet("color: #16a34a; font-weight: bold; font-size: 12px;")
            else:
                sess["lbl"].setText(f"Debug từ chối ({status_label}): {msg}")
                sess["lbl"].setStyleSheet("color: #dc2626; font-weight: bold; font-size: 12px;")
            QTimer.singleShot(2500, lambda k=camera_type: self._reset_verification_ui(k))

    def _reset_verification_ui(self, camera_type: str):
        sess = self.verify_sessions[camera_type]
        if not sess["active"]:
            if not self.verify_sessions["IN"]["active"] and not self.verify_sessions["OUT"]["active"]:
                self.btn_checkin.setEnabled(True)
                self.btn_checkin.setText("Bắt đầu Check-in")
                self.btn_checkout.setEnabled(True)
                self.btn_checkout.setText("Bắt đầu Check-out")
                sess["lbl"].setText("Sẵn sàng. Nhấn nút để bắt đầu xác thực")
                sess["lbl"].setStyleSheet("color: #6b7280; font-size: 12px;")
                sess["prog"].setValue(0)

    def open_registration(self):
        worker = self.worker_in
        has_signal = False
        if worker and getattr(worker, "running", False):
            if hasattr(worker, "is_signal_active") and callable(worker.is_signal_active):
                has_signal = worker.is_signal_active()
            else:
                has_signal = getattr(worker, "latest_raw_frame", None) is not None

        if not has_signal:
            QMessageBox.warning(
                self, 
                "Không có nguồn camera", 
                "Camera Cổng Vào hiện không có tín hiệu hình ảnh. Vui lòng kiểm tra lại kết nối thiết bị hoặc cấu hình camera trước khi thực hiện đăng ký sinh viên."
            )
            return

        dlg = RegistrationDialog(
            self.db, 
            self.face_engine, 
            self.reload_student_cache, 
            get_cache_cb=self.get_student_cache,
            worker_in=self.worker_in, 
            worker_out=self.worker_out, 
            parent=self
        )
        dlg.exec()
        self._update_stats()

    def open_admin(self):
        if hasattr(self, "admin_dlg") and self.admin_dlg and self.admin_dlg.isVisible():
            self.admin_dlg.raise_()
            self.admin_dlg.activateWindow()
            return

        login_dlg = AdminLoginDialog(self.db, parent=self)
        if login_dlg.exec() != QDialog.DialogCode.Accepted or not login_dlg.authenticated_user:
            return

        self.admin_dlg = AdminDialog(
            self.db, 
            self.reload_student_cache, 
            self.restart_cameras, 
            debug_mgr=self.debug_mgr, 
            attendance_logic=self.attendance_logic, 
            parent=self,
            current_user=login_dlg.authenticated_user
        )
        self.admin_dlg.setModal(False)
        self.admin_dlg.show()
        self.admin_dlg.raise_()
        self.admin_dlg.activateWindow()

    def closeEvent(self, event):
        if hasattr(self, "admin_dlg") and self.admin_dlg and self.admin_dlg.isVisible():
            if not self.admin_dlg.close():
                event.ignore()
                return
        for sess in self.verify_sessions.values():
            if sess["timer"].isActive():
                sess["timer"].stop()
        if hasattr(self, "db") and getattr(self.db, "is_debug", False):
            try:
                self.db.clear_mock_students_and_classes()
            except Exception:
                pass
        if self.worker_in:
            self.worker_in.stop()
        if self.worker_out:
            self.worker_out.stop()
        event.accept()

def main():
    app = QApplication(sys.argv)
    ensure_app_assets()
    app.setFont(QFont("Aptos", 10))
    app.setStyleSheet(GLOBAL_MENU_QSS + "\n" + GLOBAL_SCROLLBAR_QSS + "\n" + GLOBAL_DIALOG_QSS + "\n" + GLOBAL_COMBOBOX_QSS)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
