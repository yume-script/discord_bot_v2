import requests

def check_poring_factory_status():
    """
    http://192.168.0.50:3000 상태를 확인하여
    건물 통신 회선(카톡 브릿지 서버) 정상 여부를 반환합니다.
    (함수 이름은 예전 그대로 - 이야기 속에서는 '오아시스 마을' 건물 설비 상태로 쓰인다)
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
