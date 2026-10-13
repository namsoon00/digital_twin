"""Record refresh work through the caller's existing transaction and lease."""


def record_monitor_refresh(execute, account_id, stamp, priority=10):
    execute("""
        INSERT INTO monitor_account_jobs (
            account_id,status,priority,next_run_at,locked_by,locked_until,
            attempts,last_started_at,last_finished_at,last_error,updated_at
        ) VALUES (%s,'pending',%s,%s,'','',0,'','','',%s)
        ON DUPLICATE KEY UPDATE
            status=CASE WHEN status='processing' THEN status ELSE 'pending' END,
            priority=LEAST(priority,VALUES(priority)),
            next_run_at=VALUES(next_run_at), updated_at=VALUES(updated_at)
    """, (account_id, priority, stamp, stamp))
