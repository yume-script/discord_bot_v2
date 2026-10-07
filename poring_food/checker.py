import requests

def check_poring_factory_status():
    """
    http://192.168.0.50:3000 상태를 확인하여
    공장 정상 가동 여부를 반환합니다.
    """
    url = "http://192.168.0.50:3000//dashboard"
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            return "정상 가동 중", True
        else:
            return f"비정상 응답 ({response.status_code})", False
    except requests.exceptions.RequestException:
        return "연결 실패 (서버 다운)", False
