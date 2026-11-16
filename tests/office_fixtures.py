from docx import Document
from openpyxl import Workbook
from pptx import Presentation
from pptx.util import Inches


def make_office(path, text="Office keyword 한글", blank=False):
    extension = path.suffix.lower()
    if extension == ".docx":
        document = Document()
        if not blank:
            document.add_paragraph(text)
        document.save(path)
    elif extension == ".xlsx":
        workbook = Workbook()
        workbook.active.title = "자료"
        if not blank:
            workbook.active["A1"] = text
        workbook.save(path)
        workbook.close()
    elif extension == ".pptx":
        presentation = Presentation()
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        if not blank:
            slide.shapes.add_textbox(Inches(1), Inches(1), Inches(7), Inches(1)).text = text
        presentation.save(path)
    else:
        raise ValueError(extension)
    return path
