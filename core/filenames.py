import unicodedata


def fold_filename(value):
    """파일명 검색의 기존 NFC·Unicode casefold 규칙."""
    return unicodedata.normalize('NFC', value).casefold()
