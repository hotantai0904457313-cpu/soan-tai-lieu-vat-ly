"""
Mô hình dữ liệu tài liệu Vật lý.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import json
import uuid


SECTION_TYPES = {
    'trac_nghiem_lua_chon': 'PHẦN I. TRẮC NGHIỆM LỰA CHỌN',
    'dung_sai':             'PHẦN II. ĐÚNG – SAI',
    'tra_loi_ngan':         'PHẦN III. TRẢ LỜI NGẮN',
    'tu_luan':              'PHẦN IV. TỰ LUẬN',
    'ly_thuyet':            'LÝ THUYẾT',
    'bai_tap':              'BÀI TẬP',
    'khac':                 'NỘI DUNG KHÁC',
}

DOC_TYPES = {
    'de_thi':        'Đề kiểm tra / Đề thi',
    'phieu_bai_tap': 'Phiếu bài tập',
    'bai_giang':     'Bài giảng',
    'khac':          'Khác',
}


@dataclass
class Image:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    filename: str = ''      # tên file ảnh đã xử lý, lưu trong data/uploads/images/
    width: int = 0
    height: int = 0
    caption: str = ''

    def to_dict(self) -> Dict:
        return {'id': self.id, 'filename': self.filename,
                'width': self.width, 'height': self.height, 'caption': self.caption}

    _KEYS = ('id', 'filename', 'width', 'height', 'caption')

    @staticmethod
    def from_dict(d: Dict) -> 'Image':
        # Lọc key lạ — Image(**d) từng vỡ khi JSON có thêm trường
        return Image(**{k: v for k, v in (d or {}).items() if k in Image._KEYS})


@dataclass
class Question:
    id: str = field(default_factory=lambda: 'q_' + str(uuid.uuid4())[:8])
    number: int = 1
    text: str = ''
    options: List[str] = field(default_factory=list)    # A., B., C., D.
    sub_items: List[str] = field(default_factory=list)  # a), b), c), d) cho đúng-sai
    correct_answer: str = ''
    ai_solution: str = ''
    show_solution: bool = True
    images: List[Image] = field(default_factory=list)
    # Hình của TỪNG phương án, song song với options (câu "4 phương án là 4 đồ thị").
    # Rỗng/thiếu → coi như phương án không có hình (tương thích tài liệu cũ).
    option_images: List[List[Image]] = field(default_factory=list)
    raw_html: str = ''   # HTML gốc từ file import

    def ensure_option_images(self) -> List[List[Image]]:
        """Đưa option_images về đúng độ dài options (thêm [] / cắt bớt)."""
        n = len(self.options)
        oi = [list(x or []) for x in (self.option_images or [])][:n]
        while len(oi) < n:
            oi.append([])
        self.option_images = oi
        return oi

    def has_images(self) -> bool:
        return bool(self.images) or any(bool(x) for x in (self.option_images or []))

    def to_dict(self) -> Dict:
        return {
            'id': self.id,
            'number': self.number,
            'text': self.text,
            'options': self.options,
            'sub_items': self.sub_items,
            'correct_answer': self.correct_answer,
            'ai_solution': self.ai_solution,
            'show_solution': self.show_solution,
            'images': [img.to_dict() for img in self.images],
            'option_images': [[img.to_dict() for img in (lst or [])]
                              for lst in (self.option_images or [])],
            'raw_html': self.raw_html,
        }

    @staticmethod
    def from_dict(d: Dict) -> 'Question':
        q = Question(
            id=d.get('id', 'q_' + str(uuid.uuid4())[:8]),
            number=d.get('number', 1),
            text=d.get('text', ''),
            options=d.get('options', []),
            sub_items=d.get('sub_items', []),
            correct_answer=d.get('correct_answer', ''),
            ai_solution=d.get('ai_solution', ''),
            show_solution=d.get('show_solution', True),
            raw_html=d.get('raw_html', ''),
        )
        q.images = [Image.from_dict(i) for i in d.get('images', [])]
        q.option_images = [[Image.from_dict(i) for i in (lst or [])]
                           for lst in (d.get('option_images') or [])]
        return q


@dataclass
class Section:
    id: str = field(default_factory=lambda: 'sec_' + str(uuid.uuid4())[:8])
    type: str = 'khac'
    label: str = ''
    intro: str = ''        # phần mô tả/giới thiệu trước câu hỏi
    questions: List[Question] = field(default_factory=list)
    is_theory: bool = False  # True nếu là phần lý thuyết (giữ nguyên, AI không can thiệp)

    def to_dict(self) -> Dict:
        return {
            'id': self.id,
            'type': self.type,
            'label': self.label,
            'intro': self.intro,
            'questions': [q.to_dict() for q in self.questions],
            'is_theory': self.is_theory,
        }

    @staticmethod
    def from_dict(d: Dict) -> 'Section':
        s = Section(
            id=d.get('id', 'sec_' + str(uuid.uuid4())[:8]),
            type=d.get('type', 'khac'),
            label=d.get('label', ''),
            intro=d.get('intro', ''),
            is_theory=d.get('is_theory', False),
        )
        s.questions = [Question.from_dict(q) for q in d.get('questions', [])]
        return s


@dataclass
class Document:
    id: Optional[int] = None
    title: str = 'Tài liệu mới'
    subject: str = 'Vật lý'
    grade: Optional[int] = None
    chapter: str = ''
    semester: str = ''
    doc_type: str = 'khac'
    sections: List[Section] = field(default_factory=list)
    # Phạm vi kiến thức ràng buộc AI khi giải bài — xem core/chuong_trinh.py
    ai_scope: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            'id': self.id,
            'title': self.title,
            'subject': self.subject,
            'grade': self.grade,
            'chapter': self.chapter,
            'semester': self.semester,
            'doc_type': self.doc_type,
            'ai_scope': self.ai_scope,
            'sections': [s.to_dict() for s in self.sections],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @staticmethod
    def from_dict(d: Dict) -> 'Document':
        doc = Document(
            id=d.get('id'),
            title=d.get('title', 'Tài liệu mới'),
            subject=d.get('subject', 'Vật lý'),
            grade=d.get('grade'),
            chapter=d.get('chapter', ''),
            semester=d.get('semester', ''),
            doc_type=d.get('doc_type', 'khac'),
            ai_scope=d.get('ai_scope') or {},   # tài liệu cũ không có field này
        )
        doc.sections = [Section.from_dict(s) for s in d.get('sections', [])]
        return doc

    @staticmethod
    def from_json(s: str) -> 'Document':
        return Document.from_dict(json.loads(s))

    def count_questions(self) -> int:
        return sum(len(sec.questions) for sec in self.sections)
