class OperationCancelled(KeyboardInterrupt):
    """사용자 요청에 따른 협력적 작업 중단."""


def check_cancelled(is_cancelled):
    if is_cancelled is not None and is_cancelled():
        raise OperationCancelled()
