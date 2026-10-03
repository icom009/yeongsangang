"""관리 화면에서 사진을 메일로 다시 보낸다 (선택 기능).

설정(YS_SMTP_*)이 비어 있으면 관리 화면의 메일 단추가 꺼지고, 링크 복사·QR만 씁니다.
인터넷이 없는 현장(플랜 B)에서도 서버는 그대로 돌아갑니다.
"""
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from . import config

SUBJECT = '2026 나주영산강축제 포토부스 사진'
BODY = """안녕하세요, 2026 나주영산강축제 AI 포토부스입니다.

촬영하신 사진을 다시 보내 드립니다.

  {url}

링크는 보관 기간이 지나면 열리지 않으니 사진은 휴대폰에 저장해 주세요.
"""


def send(to, url, attachment=None, filename='yeongsangang.jpg'):
    """사진 링크를 메일로 보낸다. attachment는 보낼 JPEG 바이트(없으면 링크만)."""
    if not config.mail_ready():
        raise RuntimeError('메일 설정(YS_SMTP_HOST·YS_SMTP_FROM)이 없어요.')
    msg = EmailMessage()
    msg['Subject'] = SUBJECT
    msg['From'] = formataddr(('나주영산강축제 포토부스', config.SMTP_FROM))
    msg['To'] = to
    msg.set_content(BODY.format(url=url))
    if attachment:
        msg.add_attachment(attachment, maintype='image', subtype='jpeg', filename=filename)

    if config.SMTP_SECURITY == 'ssl':
        server = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT,
                                  context=ssl.create_default_context(), timeout=30)
    else:
        server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30)
    with server:
        if config.SMTP_SECURITY == 'starttls':
            server.starttls(context=ssl.create_default_context())
        if config.SMTP_USER:
            server.login(config.SMTP_USER, config.SMTP_PASS)
        server.send_message(msg)
    return True
