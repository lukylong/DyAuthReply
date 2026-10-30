//! Input-method preparation is a compatibility hint, not an authorization gate.
//! Keep real browser/Agent errors intact even when input preparation fails.
pub async fn start_after_attempt<T, E, P, S, F, W>(prepare: P, start: S, warn: W) -> Result<T, E>
where
    P: std::future::Future<Output = Result<(), String>>,
    S: FnOnce() -> F,
    F: std::future::Future<Output = Result<T, E>>,
    W: FnOnce(String),
{
    if let Err(warning) = prepare.await {
        warn(warning);
    }
    start().await
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        cell::RefCell,
        future::Future,
        task::{Context, Poll, Waker},
    };

    fn run<T>(future: impl Future<Output = T>) -> T {
        match std::pin::pin!(future).poll(&mut Context::from_waker(Waker::noop())) {
            Poll::Ready(value) => value,
            Poll::Pending => panic!("test uses only immediately ready futures"),
        }
    }

    #[test]
    fn successful_switch_starts_once_after_preparation() {
        let events = RefCell::new(Vec::new());
        let result: Result<u32, String> = run(start_after_attempt(
            async {
                events.borrow_mut().push("prepare");
                Ok(())
            },
            || async {
                events.borrow_mut().push("start");
                Ok(42)
            },
            |_| events.borrow_mut().push("warning"),
        ));
        assert_eq!(result, Ok(42));
        assert_eq!(*events.borrow(), ["prepare", "start"]);
    }

    #[test]
    fn reported_os_zero_or_timeout_never_blocks_the_agent_request() {
        for warning in [
            "校验英文键盘，操作成功完成。(os error 0)",
            "切换超时",
            "窗口焦点变化",
        ] {
            let events = RefCell::new(Vec::new());
            let result: Result<u32, String> = run(start_after_attempt(
                async { Err(warning.to_string()) },
                || async {
                    events.borrow_mut().push("start");
                    Ok(42)
                },
                |actual| {
                    assert_eq!(actual, warning);
                    events.borrow_mut().push("warning");
                },
            ));
            assert_eq!(result, Ok(42));
            assert_eq!(*events.borrow(), ["warning", "start"]);
        }
    }

    #[test]
    fn real_browser_failure_is_not_swallowed_or_retried() {
        let mut starts = 0;
        let result: Result<(), &str> = run(start_after_attempt(
            async { Err("layout mismatch".into()) },
            || {
                starts += 1;
                async { Err("browser exit 3") }
            },
            |_| {},
        ));
        assert_eq!(starts, 1);
        assert_eq!(result, Err("browser exit 3"));
    }
}
