"""/개미소리: 글이 길어도 말풍선 칸 안에 들어가게 줄을 나누고 글자 크기를 줄인다."""
from PIL import Image, ImageDraw, ImageFont

from core import ant_voice


def test_wrap_fits_box_and_keeps_text():
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    font = ImageFont.load_default(size=40)
    text = "세금 또 올린다는 영주 진짜 너무한 거 아니냐 이번 달만 세 번째야"
    lines = ant_voice._wrap(draw, text, font, ant_voice.BOX_W)
    assert len(lines) > 1
    assert all(draw.textlength(ln, font=font) <= ant_voice.BOX_W for ln in lines)
    assert "".join(lines).replace(" ", "") == text.replace(" ", "")
    # 띄어쓰기 없는 긴 글도 글자 단위로 나눈다
    long = "가" * 40
    lines = ant_voice._wrap(draw, long, font, ant_voice.BOX_W)
    assert "".join(lines) == long and all(draw.textlength(ln, font=font) <= ant_voice.BOX_W for ln in lines)
