"""사용자 파일과 무관한 작은 합성 문서를 생성한다."""
import codecs


MIXED_KINDS = ('txt_utf8', 'txt_utf16', 'pdf', 'docx', 'xlsx', 'pptx')
SUFFIXES = {'txt_utf8': '.txt', 'txt_utf16': '.txt', 'pdf': '.pdf', 'docx': '.docx', 'xlsx': '.xlsx', 'pptx': '.pptx'}


def format_token(kind):
    return 'bench' + kind.replace('_', '')


def write_document(path, kind, index, keyword='benchmarkneedle'):
    content = f'{keyword} {format_token(kind)} local content {index} 한글\n'
    if kind == 'txt_utf8':
        path.write_bytes(content.encode('utf-8'))
    elif kind == 'txt_utf16':
        path.write_bytes(codecs.BOM_UTF16_LE + content.encode('utf-16-le'))
    elif kind == 'pdf':
        import pymupdf
        with pymupdf.open() as document:
            document.new_page().insert_text((72, 72), f'{keyword} {format_token(kind)} local content {index}')
            document.new_page().insert_text((72, 72), 'Synthetic second page')
            document.save(path, deflate=True)
    elif kind == 'docx':
        from docx import Document
        document = Document()
        document.add_paragraph(content)
        table = document.add_table(rows=2, cols=2)
        table.cell(0, 0).text = '합성 표'
        table.cell(1, 1).text = 'Local table content'
        document.save(path)
    elif kind == 'xlsx':
        from openpyxl import Workbook
        workbook = Workbook()
        try:
            workbook.active.title = '자료'
            workbook.active.append(['합성 시트', content])
            workbook.active.append([1, 2])
            workbook.active['A3'] = '=SUM(1,2)'
            workbook.save(path)
        finally:
            workbook.close()
    elif kind == 'pptx':
        from pptx import Presentation
        from pptx.util import Inches
        presentation = Presentation()
        for text in (content, '합성 두 번째 슬라이드'):
            slide = presentation.slides.add_slide(presentation.slide_layouts[6])
            slide.shapes.add_textbox(Inches(1), Inches(1), Inches(7), Inches(2)).text = text
        presentation.save(path)
    else:
        raise ValueError(kind)
