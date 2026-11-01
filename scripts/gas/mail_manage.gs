// ==========================================
// 메일 관리 (Google Apps Script) - 알림대기 메일 알림 + 오래된 메일 정리
//
// NOTICE_MODE
//   "01" 기존 방식: 📣│알림방에 사람이 보는 알림을 그대로 올린다.
//   "02" 숨김 방식: 알림방에는 아무것도 안 올리고, 봇만 보는 비공개 채널에
//        "poring-mail kind=<종류> count=<개수>" 한 줄만 보낸다 (메일 제목/미리보기/인증 코드는 보내지 않음).
//        봇(discord_bot_v2 poring_food/street_mail.py)이 읽고 지운 뒤
//        오아시스 마을의 거리(성벽) 청소/전령 우편함 이야기로만 남긴다.
//   02를 쓰려면: 디스코드에 비공개 채널을 하나 만들고(봇만 보기/메시지 관리 권한), 게이트웨이가 그 채널에 보낼 수 있게
//   웹훅을 만든 뒤 아래 DISCORD_HIDDEN_CHANNEL_ID에 채널 ID를, etc .env에 PORING_MAIL_HIDDEN_CHANNEL_ID=같은 ID 를 넣는다.
// ==========================================
const NOTICE_MODE = "02";   // "01" 또는 "02"

// [변경됨] 통합 웹앱 게이트웨이 주소
const GATEWAY_URL = "https://script.google.com/macros/s/AKfycbwHlqIQQbW-LgOvLTr33srVvGuiXEJNCJaK56DyVTyTUPr_t6yj74VUVuByfOWYPpL1wA/exec";

// 01: 메시지를 보낼 대상 채널 ID 및 스레드 ID (📣│알림방 일반 채널로 전송되도록 null로 설정)
const DISCORD_PARENT_CHANNEL_ID = "591180628842774554"; // 📣│알림방 (웹훅이 생성된 부모 채널)
const DISCORD_WEBHOOK_THREAD_ID = null; // 스레드를 사용하지 않으려면 null

// 02: 봇만 보는 비공개 채널 (여기에 웹훅이 있어야 한다)
const DISCORD_HIDDEN_CHANNEL_ID = "여기에_비공개_채널_ID";


// ==========================================
// [메인 프로세스]
// ==========================================
function mail_manage_process() {
  // 1. 알림대기 라벨 처리
  const label_Before_Notice = GmailApp.getUserLabelByName("03_잡동사니/알림대기");
  if (label_Before_Notice && label_Before_Notice.getThreads().length > 0) {
    handleBeforeNotice(label_Before_Notice);
  }

  // 2. 알림완료 라벨 정리
  const label_After_Notice = GmailApp.getUserLabelByName("03_잡동사니/알림완료");
  if (label_After_Notice && label_After_Notice.getThreads().length > 0) {
    cleanupOldMails("알림완료 메일 정리.GAS", label_After_Notice);
  }

  // 3. 검토후삭제 라벨 정리
  const label_After_Report = GmailApp.getUserLabelByName("03_잡동사니/검토후삭제");
  if (label_After_Report && label_After_Report.getThreads().length > 0) {
    cleanupOldMails("검토완료 메일 정리.GAS", label_After_Report);
  }
}


// ==========================================
// [함수] 오래된 메일 휴지통 정리 (스레드의 '마지막 메시지' 날짜 기준 15일)
// ==========================================
function cleanupOldMails(title, label) {
  const now = new Date();
  const threads = label.getThreads();
  let countToTrash = 0;

  for (const thread of threads) {
    const age = now - thread.getLastMessageDate();
    if (age > 15 * 24 * 60 * 60 * 1000) {
      thread.removeLabel(label);
      thread.moveToTrash();
      countToTrash++;
    }
  }

  if (countToTrash > 0) {
    if (NOTICE_MODE === "02") {
      reportToVillage("trash", countToTrash);
    } else {
      postMessageToDiscord(title, `🗑️ ${countToTrash}개의 메일을 휴지통으로 이동하였습니다.`);
    }
  }
}


// ==========================================
// [함수] 메일 종류 판별 (제목 기준) -> 마을 신호 종류
// ==========================================
function noticeKind(subject) {
  if (subject === '트래픽 사용 내역 안내') return "traffic";
  if (subject === '[NAS알림] 작업 스케줄러가 예약 작업을 완료함') return "nas";
  if (subject === 'Google 데이터 다운로드 가능') return "takeout";
  if (subject === 'Monitor is DOWN: dohyun_server') return "server_down";
  if (subject.includes('인증') || subject === '이메일 확인 코드') return "registered";
  return "mail";
}


// ==========================================
// [함수] 알림대기 메일 처리 및 알림 발송 (처리한 스레드는 읽음 + 알림완료 라벨)
// ==========================================
function handleBeforeNotice(label) {
  const threads = label.getThreads();
  const doneLabel = GmailApp.getUserLabelByName("03_잡동사니/알림완료");
  const title = "📥 알림대기메일관리";
  const counts = {};

  for (const thread of threads) {
    const message = thread.getMessages()[0];
    const subject = message.getSubject();
    const kind = noticeKind(subject);

    if (NOTICE_MODE === "02") {
      counts[kind] = (counts[kind] || 0) + 1;   // 제목/본문은 보내지 않고 종류별 개수만
    } else {
      const bodyPreview = message.getPlainBody().substring(0, 100).replace(/\n/g, ' ');
      let noticeMessage = "";
      if (kind === "traffic") {
        noticeMessage = "**🚨 일일 기본 제공량을 모두 사용했습니다.**";
      } else if (kind === "nas") {
        noticeMessage = "**📁 예약 작업이 정상적으로 완료되었습니다.**";
      } else if (kind === "takeout") {
        noticeMessage = "**📦 Google Takeout**\n> 다운로드 가능한 데이터가 있습니다.";
      } else if (kind === "server_down") {
        noticeMessage = "**🔴 서버 다운 알림**\n> `dohyun_server` 네트워크 연결이 끊어졌습니다.";
      } else if (kind === "registered") {
        noticeMessage = `**📨 이메일 인증 코드 도착**\n> 제목: ${subject}`;
      } else {
        noticeMessage = `**📌 새 메일 도착**\n> 제목: ${subject}\n> 미리보기: ${bodyPreview}`;
      }
      postMessageToDiscord(title, noticeMessage);
    }

    thread.markRead();
    thread.removeLabel(label);
    if (doneLabel) {
      thread.addLabel(doneLabel);
    }
  }

  if (NOTICE_MODE === "02") {
    for (const kind in counts) {
      reportToVillage(kind, counts[kind]);
    }
  }
}


// ==========================================
// [02] 봇만 보는 비공개 채널로 마을 신호 한 줄 (종류와 개수만)
// ==========================================
function reportToVillage(kind, count) {
  sendViaGateway(DISCORD_HIDDEN_CHANNEL_ID, null, {
    username: "포링 우편함",
    content: `poring-mail kind=${kind} count=${count}`,
  }, `마을 신호 ${kind}`);
}


// ==========================================
// [01] 디스코드 메시지 전송 (통합 웹앱 게이트웨이 연동)
// ==========================================
function postMessageToDiscord(title, message) {
  if (!message || message.trim() === "") {   // 400 에러 예방
    console.warn(`[경고] '${title}'에 보낼 메시지 내용이 비어 있어 전송을 취소합니다.`);
    return;
  }
  sendViaGateway(DISCORD_PARENT_CHANNEL_ID, DISCORD_WEBHOOK_THREAD_ID, {
    username: title,
    avatar_url: "https://i.imgur.com/SoYd1BL.png",
    content: message,
  }, title);
}


function sendViaGateway(parentId, threadId, payload, label) {
  const requestUrl = `${GATEWAY_URL}?parentId=${parentId}&threadId=${threadId || "NULL"}`;
  const options = {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  };
  try {
    const response = UrlFetchApp.fetch(requestUrl, options);
    const responseCode = response.getResponseCode();
    if (responseCode >= 200 && responseCode < 300) {
      console.log(`게이트웨이 전송 완료. (${label})`);
    } else {
      console.error(`게이트웨이 전송 실패 (HTTP ${responseCode}): ${response.getContentText()}`);
    }
  } catch (error) {
    console.error(`디스코드 게이트웨이 통신 에러:`, error.toString());
  }
  Utilities.sleep(1000);   // Rate Limit 예방용 대기
}
