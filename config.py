import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
UPLOAD_DIR = os.path.join(DATA_DIR, 'uploads')
EXPORT_DIR = os.path.join(DATA_DIR, 'exports')
DB_PATH = os.path.join(DATA_DIR, 'documents.db')
STATIC_DIR = os.path.join(BASE_DIR, 'static')
LOGO_PATH = os.path.join(STATIC_DIR, 'logo.png')

# Stylesheet MathML -> OMML của Office (dùng khi xuất Word có Equation thật).
# Để rỗng -> core/latex_omml.py tự dò tìm trong Program Files.
MML2OMML_XSL = r'C:\Program Files\Microsoft Office\root\Office16\MML2OMML.XSL'

# Thông tin trường — thay đổi trong trang Cài đặt
DEFAULT_SETTINGS = {
    'teacher_name':  'Giáo viên',
    'school_name':   'Trường THPT',
    'font_body':     'Times New Roman',
    'font_size_body': '14',
    'font_size_solution': '13',
    'save_dir':      os.path.join(os.path.expanduser('~'), 'Downloads'),
    'onedrive_dir':  '',
    'gdrive_dir':    '',
    'gemini_api_key': '',
    'claude_api_key': '',
    'niner_router_key': '',
    'niner_router_url': 'http://localhost:20128/v1',
    'model_theory':  'ag/gemini-3.7-flash-low',
    'model_exercise': 'ag/gemini-3.7-flash-low',
}

ALLOWED_EXTENSIONS = {'pdf', 'docx', 'doc', 'jpg', 'jpeg', 'png', 'gif', 'webp', 'tiff', 'bmp'}
IMAGE_EXTENSIONS   = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'tiff', 'bmp'}
MAX_CONTENT_LENGTH = 1024 * 1024 * 1024  # 1 GB — PDF scan vài trăm trang có thể rất nặng
