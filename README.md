# UniFace Student

Ứng dụng desktop điểm danh sinh viên bằng nhận diện khuôn mặt, viết bằng Python + PyQt6.

Hệ thống dùng một camera tại cửa lớp để check-in/check-out, một camera gắn tường để đếm sĩ số thời gian thực, lưu dữ liệu vào SQLite và hỗ trợ quản trị tài khoản, ca học, báo cáo Excel.

## Tính năng chính

- **Điểm danh khuôn mặt**: phát hiện bằng SCRFD, trích xuất embedding 512 chiều bằng ArcFace, so khớp cosine similarity.
- **Xác thực theo phiên**: quét trong 3 giây (cấu hình được), yêu cầu tỷ lệ frame khớp tối thiểu 70% để tránh nhận nhầm.
- **Lọc khuôn mặt**: chỉ xử lý khuôn mặt lớn nhất/gần camera nhất; kiểm tra kích thước mặt, độ nghiêng đầu (roll) và độ quay mặt (yaw).
- **Chống ảnh giả**: tuỳ chọn MiniFASNet (mặc định tắt).
- **Đăng ký sinh viên**: chụp trực tiếp từ camera hoặc chọn ảnh; lưu embedding và ảnh chân dung.
- **Quản lý lớp, ca học, sinh viên**: thêm/xoá lớp, phân công lớp cho giáo viên, sửa thông tin sinh viên.
- **Báo cáo điểm danh**: lọc theo ngày và lớp, hiển thị cột ca học; xuất Excel có định dạng.
- **Đếm sĩ số**: YOLOv8n + ByteTrack/FastTrack trên camera gắn tường, hiển thị số người và biến động theo thời gian thực (không ghi vào CSDL).
- **Quản trị**: 4 vai trò, khoá tài khoản sau 5 lần đăng nhập sai, nhật ký thao tác (audit log).
- **Chế độ Debug**: thời gian ảo, giả lập quét, tạo dữ liệu mẫu, bảng điểm danh thử nghiệm tách biệt.
- **Bảo vệ dữ liệu sinh trắc học**: embedding và ảnh lưu dưới dạng mã hoá AES-256-GCM.

## Công nghệ

| Thành phần | Vai trò |
|---|---|
| Python 3.10+ (đã chạy với 3.13.7) | Ngôn ngữ chính |
| PyQt6 | Giao diện desktop |
| OpenCV | Đọc camera, xử lý ảnh |
| UniFace 4.0.0 | SCRFD, ArcFace, MiniFASNet |
| Ultralytics + PyTorch | YOLOv8n và tracking |
| SQLite | Cơ sở dữ liệu cục bộ |
| pandas + XlsxWriter | Xuất báo cáo Excel |
| cryptography | Mã hoá AES-256-GCM |

## Cấu trúc thư mục

```text
UniFace_Release/
├── app.py                 # Giao diện chính, các dialog, MainWindow
├── camera_worker.py       # Luồng camera nhận diện và luồng đếm sĩ số
├── attendance_logic.py    # Quy tắc ca học, check-in/check-out, kiểm tra cấu hình
├── face_engine.py         # SCRFD + ArcFace + MiniFASNet
├── db.py                  # SQLite, migration, mã hoá, tài khoản, audit log
├── assets/                # Icon giao diện
├── configs/
│   ├── bytetrack_classroom_062.yaml
│   └── fasttrack_classroom_075.yaml
├── data/
│   ├── attendance.db      # CSDL đang dùng
│   └── photos/            # Ảnh sinh viên, lưu dạng .enc
├── models/
│   └── yolov8n_scuthead.pt
└── README.md
```

## Cài đặt

Yêu cầu: Python 3.10 trở lên; webcam hoặc camera IP. Môi trường đã kiểm thử: Windows 10/11, Python 3.13.

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install PyQt6 opencv-python numpy pandas XlsxWriter cryptography "uniface[cpu]" ultralytics
```

Ghi chú:

- `uniface[cpu]` kéo theo `onnxruntime`; nếu thiếu, ứng dụng không khởi tạo được `FaceEngine`.
- `ultralytics` kéo theo PyTorch; lần đầu cài có thể mất vài phút.
- Trọng số SCRFD/ArcFace/MiniFASNet được UniFace tải tự động về `~/.uniface/models` ở lần chạy đầu (cần Internet). Có thể đổi thư mục cache bằng biến môi trường `UNIFACE_CACHE_DIR`.
- Model đếm sĩ số `models/yolov8n_scuthead.pt` đã có sẵn trong thư mục dự án.

## Chạy ứng dụng

Chạy từ thư mục gốc của dự án để các đường dẫn tương đối (`data/`, `assets/`) hoạt động đúng:

```powershell
python app.py
```

Lần chạy đầu, ứng dụng tự tạo/ nâng cấp `data/attendance.db`, tạo 2 ca học mặc định và tài khoản quản trị:

- Tài khoản: `admin`
- Mật khẩu: `admin123`

Tài khoản mặc định chỉ được tạo khi CSDL chưa có tài khoản quản trị nào. Nếu dùng lại `data/attendance.db` đã có dữ liệu, mật khẩu có thể đã được đổi. Hãy đổi mật khẩu ngay sau lần đăng nhập đầu tại tab **Thông tin Người Quản trị**.

## Cấu hình camera

Mở **Quản trị Hệ thống** → tab **Cấu hình**.

Mỗi camera có thể là:

- Chỉ số webcam USB: `0`, `1`, `2`, ...
- URL camera IP: `http://192.168.1.10:4747/video` hoặc `rtsp://...`
- Tệp video: `.mp4`, `.avi`, `.mkv`, `.mov`

| Khoá cấu hình | Camera | Vai trò |
|---|---|---|
| `camera_in` | Camera cửa lớp | Dùng cho cả nút **Check-in** và **Check-out** |
| `camera_out` | Camera gắn tường | Chỉ đếm sĩ số; tên khoá là tên cũ, không phải cổng ra thứ hai |

Sau khi lưu cấu hình, ứng dụng tự khởi động lại các luồng camera. Cả hai camera đều mở khi khởi động ứng dụng, kể cả khi chưa đăng nhập quản trị.

Các ngưỡng đáng chú ý:

- **Ngưỡng nhận diện khuôn mặt**: mặc định `0.55` (cao hơn = khắt khe hơn).
- **Thời gian xác thực**: mặc định `3.0` giây.
- **Tỷ lệ frame hợp lệ**: mặc định `70%`.
- **Kích thước mặt tối thiểu**: mặc định `18%` chiều cao khung hình.
- **Độ khắt khe góc nhìn**: Dễ / Tiêu chuẩn / Nghiêm ngặt / Tùy chỉnh.
- **Chống ảnh giả**: mặc định tắt; có thể chỉnh ngưỡng tin cậy.
- **Ngưỡng tin cậy đếm người**: mặc định `0.25`.

## Quy trình sử dụng

1. **Đăng ký sinh viên**: nhấn **Đăng ký Sinh viên**, nhập MSSV, họ tên, chọn lớp; giữ mặt nhìn thẳng và chụp ảnh. Cần camera cửa lớp có tín hiệu.
2. **Check-in**: nhấn **Bắt đầu Check-in**, giữ mặt trước camera đến khi thanh tiến trình hoàn tất. Hệ thống chỉ ghi nhận khi tỷ lệ frame khớp đạt ngưỡng.
3. **Check-out**: nhấn **Bắt đầu Check-out** tại cùng camera cửa lớp. Check-out yêu cầu đã có check-in trong ca.
4. **Theo dõi**: bảng **Nhật Ký Điểm Danh** và thẻ **Sinh Viên Gần Nhất** cập nhật ngay; camera gắn tường hiển thị sĩ số.
5. **Báo cáo**: vào **Quản trị** → **Báo cáo Điểm danh**, chọn ngày/lớp, xuất Excel.

Nút Check-in và Check-out không chạy đồng thời; mỗi phiên xác thực dùng camera cửa lớp.

## Quy tắc điểm danh

Mỗi ca học có các mốc thời gian:

| Trường | Ý nghĩa |
|---|---|
| `checkin_open` | Giờ mở cổng điểm danh |
| `class_start` | Giờ bắt đầu ca |
| `late_threshold` | Hạn chót tính đúng giờ |
| `checkin_cutoff` | Giờ đóng cổng; sau mốc này ghi nhận **Vắng** |
| `early_threshold` | Trước mốc này khi check-out bị tính **Về sớm** |
| `class_end` | Giờ kết thúc ca |
| `transition_cutoff` | Giờ chuyển sang ca kế tiếp |

Thứ tự hợp lệ bắt buộc:

```text
checkin_open < class_start <= late_threshold <= checkin_cutoff
             <= early_threshold <= class_end <= transition_cutoff
```

Ứng dụng kiểm tra thứ tự này và kiểm tra các ca không chồng lấn trước khi lưu cấu hình.

Hai ca mặc định:

- **Ca 1**: mở cổng `06:30`, bắt đầu `07:30`, kết thúc `11:30`.
- **Ca 2**: mở cổng `12:30`, bắt đầu `13:30`, kết thúc `17:30`.

Trạng thái điểm danh:

| Mã | Hiển thị | Khi nào |
|---|---|---|
| `DUNG_GIO` | Đúng giờ | Check-in từ giờ mở cổng đến `late_threshold` |
| `DI_TRE` | Đi trễ | Check-in sau `late_threshold` nhưng trước `checkin_cutoff` |
| `VANG` | Vắng mặt | Check-in sau `checkin_cutoff`; không thể check-out để xoá trạng thái |
| `VE_SOM` | Về sớm | Check-out trước `early_threshold` |
| `HOAN_THANH` | Hoàn thành | Check-out sau `early_threshold` |
| `CANH_BAO` | Cảnh báo người lạ | Người chưa đăng ký xuất hiện liên tục trước camera |

Các trường hợp bị từ chối: chưa đến giờ mở cổng (`TOO_EARLY`), đã điểm danh (`ALREADY_CHECKED_IN`), đã check-out (`ALREADY_CHECKED_OUT`), chưa check-in (`NO_CHECK_IN`), ca đã kết thúc (`CLASS_ENDED`), đang trong thời gian chờ quét lại (`COOLDOWN`, mặc định 60 giây mỗi sinh viên/cổng).

## Phân quyền

| Vai trò | Quyền chính |
|---|---|
| Quản trị viên cấp cao (`super_admin`) | Toàn bộ tab, quản lý người dùng, audit log, cấu hình, debug |
| Giáo vụ (`manager`) | Sinh viên, điểm danh, quản lý lớp; không vào Cấu hình/Debug |
| Giáo viên (`teacher`) | Chỉ xem và quản lý sinh viên/điểm danh thuộc lớp được phân công |
| Kỹ thuật viên (`technician`) | Cấu hình camera/hệ thống; không xem dữ liệu sinh viên và điểm danh |

Tài khoản bị khoá tạm 15 phút sau 5 lần nhập sai mật khẩu liên tiếp.

## Dữ liệu và bảo mật

CSDL `data/attendance.db` dùng chế độ WAL và gồm các bảng chính:

- `students`: sinh viên, embedding đã mã hoá, ảnh chân dung.
- `classes`: danh mục lớp.
- `sessions`: ca học.
- `attendance`: điểm danh thật.
- `attendance_debug`: điểm danh thử nghiệm của chế độ Debug.
- `settings`: cấu hình hệ thống.
- `alerts`: cảnh báo người lạ.
- `admin_users`, `admin_user_classes`, `admin_audit_logs`: tài khoản, phân công lớp, nhật ký quản trị.

Bảo vệ dữ liệu:

- Embedding 512 chiều được mã hoá AES-256-GCM trước khi ghi vào SQLite.
- Ảnh sinh viên được nén JPEG, mã hoá và lưu thành tệp `.enc` trong `data/photos/`.
- Mật khẩu quản trị dùng PBKDF2-HMAC-SHA256, 600.000 vòng, muối ngẫu nhiên.
- Nên sao lưu cả `data/attendance.db` và `data/photos/` khi chuyển máy; đóng ứng dụng trước khi sao chép.

> **Lưu ý bảo mật:** khoá AES hiện được khai báo cố định trong `db.py` (`LOCAL_SECRET_KEY`). Trước khi triển khai thật, hãy thay bằng cơ chế quản lý khoá riêng. Nếu đổi khoá, dữ liệu embedding/ảnh cũ sẽ không giải mã được.

Không đưa `data/attendance.db` hoặc `data/photos/` chứa dữ liệu sinh viên thật lên kho mã nguồn công khai.

## Chế độ Debug

Chỉ `super_admin` sử dụng được. Chế độ này phục vụ kiểm thử nghiệp vụ mà không ảnh hưởng dữ liệu thật:

- Đồng hồ ảo độc lập với giờ Windows; có thể đóng băng thời gian, nhảy nhanh đến các mốc ca học.
- Giả lập quét check-in/check-out cho sinh viên có trong danh sách.
- Giả lập người lạ để kiểm tra cảnh báo.
- Tạo/xoá sinh viên và lớp ảo (`is_mock = 1`).
- Điểm danh Debug ghi vào `attendance_debug`, không lẫn với báo cáo thật.
- Tuỳ chọn cho phép quét lặp lại nhiều lần để test nhanh.
- Khi tắt Debug hoặc thoát ứng dụng trong lúc Debug đang bật, dữ liệu ảo được dọn tự động.

## Xử lý sự cố

| Hiện tượng | Cách xử lý |
|---|---|
| Camera báo “KHONG CO TIN HIEU CAMERA” | Kiểm tra lại chỉ số webcam/URL/đường dẫn tệp; đóng ứng dụng khác đang dùng camera; thử `0`, `1`, `2`; kiểm tra camera IP cùng mạng |
| Không đăng ký được sinh viên | Camera cửa lớp phải có tín hiệu; kiểm tra `camera_in` |
| `ModuleNotFoundError: uniface` hoặc lỗi `onnxruntime` | Cài lại `pip install "uniface[cpu]"` |
| Lần chạy đầu bị treo hoặc lỗi tải model | Cần Internet để tải trọng số UniFace; kiểm tra `~/.uniface/models` hoặc đặt `UNIFACE_CACHE_DIR` |
| Xuất Excel báo lỗi | Cài `XlsxWriter`; đóng tệp Excel đang mở trước khi xuất |
| Không đăng nhập được | Sau 5 lần sai, chờ 15 phút hoặc dùng tài khoản `super_admin` khác |
| Camera gắn tường không đếm | Kiểm tra `models/yolov8n_scuthead.pt`, đã cài `ultralytics`; hạ ngưỡng tin cậy; chỉnh góc camera để thấy đủ lớp |
| CSDL báo bận/locked | Chỉ chạy một instance ứng dụng; đóng tiến trình còn giữ file `attendance.db` |

## Giới hạn đã biết

- Ứng dụng desktop một máy, chưa có đồng bộ client–server.
- Đếm sĩ số là ước lượng theo khung hình, có thể sai khi bị che khuất hoặc camera đặt góc xấu; số đếm không được lưu vào CSDL.
- Khoá mã hoá đang cố định trong mã nguồn (xem mục bảo mật).
- Dự án chưa kèm bộ test tự động và chưa có `requirements.txt`; cài theo lệnh ở trên.
