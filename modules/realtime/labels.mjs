function realtimeEventLabel(name) {
  return {
    "realtime.connected": "웹소켓 연결",
    "realtime.status": "실시간 상태",
    "settings.updated": "설정 변경",
    "account.saved": "계정 저장",
    "account.removed": "계정 삭제",
    "notification_template.updated": "알림 템플릿",
    "notification_rule.updated": "알림 룰",
    "notification.test_requested": "테스트 알림 요청",
    "notification.job_queued": "알림 큐 적재",
    "monitoring.snapshot_collected": "모니터링 스냅샷",
    "monitoring.alerts_detected": "새 투자 신호 감지",
    "monitoring.cycle_completed": "모니터링 사이클",
    "research_evidence.collected": "새 뉴스·공시 근거",
    "symbol_universe.refresh_requested": "전체 종목 갱신 시작",
    "symbol_universe.refresh_failed": "전체 종목 갱신 실패",
    "symbol_universe.refreshed": "전체 종목 갱신"
  }[name] || name || "-";
}

export { realtimeEventLabel };
