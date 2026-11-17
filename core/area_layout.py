"""파일 수의 면적 비율을 보존하는 이진 분할 treemap."""


def layout_areas(weights, rect=(0.0, 0.0, 1000.0, 560.0)):
    if not weights:
        return []
    if any(type(v) is not int or v <= 0 for v in weights):
        raise ValueError('영역 가중치는 양의 정수여야 합니다.')
    result = [None] * len(weights)
    def split(start, end, x, y, width, height):
        if end - start == 1:
            result[start] = (x, y, width, height)
            return
        total = sum(weights[start:end])
        partial = 0
        cut = start + 1
        best = float('inf')
        for i in range(start, end - 1):
            partial += weights[i]
            distance = abs(total - 2 * partial)
            if distance < best:
                best, cut = distance, i + 1
        ratio = sum(weights[start:cut]) / total
        if width >= height:
            first = width * ratio
            split(start, cut, x, y, first, height)
            split(cut, end, x + first, y, width - first, height)
        else:
            first = height * ratio
            split(start, cut, x, y, width, first)
            split(cut, end, x, y + first, width, height - first)
    split(0, len(weights), *rect)
    return result
