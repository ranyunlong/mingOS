//! Private persistent-session budgets; elapsed durations come from parent Instant.
use std::{
    collections::VecDeque,
    time::{Duration, Instant},
};
pub(crate) const FUEL_CAP: u64 = 300_000_000;
pub(crate) const FUEL_RATE: u64 = 100_000_000;
#[derive(Debug)]
pub(crate) struct TokenBucket {
    cap: u64,
    rate: u64,
    tokens: u64,
    fraction: u128,
    last: Duration,
}
impl TokenBucket {
    pub fn new(cap: u64, rate: u64, initial: u64, now: Duration) -> Self {
        Self {
            cap,
            rate,
            tokens: initial.min(cap),
            fraction: 0,
            last: now,
        }
    }
    fn refill(&mut self, now: Duration) {
        if now <= self.last {
            return;
        }
        let produced = (now - self.last)
            .as_nanos()
            .saturating_mul(self.rate as u128)
            .saturating_add(self.fraction);
        self.last = now;
        let added = produced / 1_000_000_000;
        self.fraction = produced % 1_000_000_000;
        self.tokens = (u128::from(self.tokens) + added).min(self.cap as u128) as u64;
        if self.tokens == self.cap {
            self.fraction = 0;
        }
    }
    pub fn take(&mut self, wanted: u64, now: Duration) -> u64 {
        self.refill(now);
        let grant = wanted.min(self.tokens);
        self.tokens -= grant;
        grant
    }
}
#[derive(Debug)]
pub(crate) struct FuelBudget {
    bucket: TokenBucket,
    pub total: u64,
    cap: u64,
}
impl FuelBudget {
    pub fn new(initial: u64) -> Self {
        Self {
            bucket: TokenBucket::new(initial, FUEL_RATE, 0, Duration::ZERO),
            total: initial,
            cap: initial,
        }
    }
    pub fn grant(&mut self, remaining: u64, now: Duration) -> Result<u64, &'static str> {
        let deficit = self
            .cap
            .checked_sub(remaining)
            .ok_or("PROTOCOL_FUEL_REMAINING")?;
        let grant = self.bucket.take(deficit, now);
        self.total = self
            .total
            .checked_add(grant)
            .ok_or("PROTOCOL_FUEL_OVERFLOW")?;
        Ok(grant)
    }
}
/// Worker-side validation; parent and worker independently accumulate all issued fuel.
#[derive(Debug)]
pub(crate) struct WorkerFuel {
    pub cap: Option<u64>,
    pub total: u64,
}
impl WorkerFuel {
    pub fn new(initial: u64, cap: Option<u64>) -> Result<Self, &'static str> {
        if cap.is_some_and(|c| c == 0 || c > FUEL_CAP || c != initial) {
            return Err("PROTOCOL_FUEL_CAP");
        }
        Ok(Self {
            cap,
            total: initial,
        })
    }
    pub fn apply(&mut self, remaining: u64, grant: Option<u64>) -> Result<u64, &'static str> {
        match (self.cap, grant) {
            (None, None) => Ok(remaining),
            (Some(cap), Some(grant)) => {
                let next = remaining
                    .checked_add(grant)
                    .filter(|n| *n <= cap)
                    .ok_or("PROTOCOL_FUEL_GRANT")?;
                let total = self
                    .total
                    .checked_add(grant)
                    .ok_or("PROTOCOL_FUEL_OVERFLOW")?;
                self.total = total;
                Ok(next)
            }
            _ => Err("PROTOCOL_FUEL_GRANT"),
        }
    }
}
#[derive(Debug)]
pub(crate) struct RequestBudget {
    times: VecDeque<Duration>,
    limit: usize,
}
impl RequestBudget {
    pub fn new(limit: u64) -> Self {
        Self {
            times: VecDeque::new(),
            limit: limit.min(32768) as usize,
        }
    }
    pub fn accept(&mut self, now: Duration) -> Result<(), &'static str> {
        while self
            .times
            .front()
            .is_some_and(|t| now.saturating_sub(*t) >= Duration::from_secs(60))
        {
            self.times.pop_front();
        }
        if self.times.len() >= self.limit {
            return Err("REQUEST_LIMIT");
        }
        self.times.push_back(now);
        Ok(())
    }
}
/// Wall service-time admission: 1s burst, 500ms/s. This is not CPU accounting.
pub(crate) struct ServiceBudget {
    bucket: TokenBucket,
}
impl ServiceBudget {
    pub fn new() -> Self {
        Self {
            bucket: TokenBucket::new(1_000_000_000, 500_000_000, 1_000_000_000, Duration::ZERO),
        }
    }
    pub fn charge(&mut self, work: Duration, now: Duration) -> Duration {
        let cost = work.as_nanos().min(u64::MAX as u128) as u64;
        let available = self.bucket.take(cost, now);
        let delay = Duration::from_nanos(cost.saturating_sub(available).saturating_mul(2));
        self.bucket.last = now.saturating_add(delay);
        delay
    }
}
pub(crate) struct Progress {
    pub last: Instant,
    pub parked: bool,
    pub initialized: bool,
}
impl Progress {
    pub fn new() -> Self {
        Self {
            last: Instant::now(),
            parked: false,
            initialized: false,
        }
    }
    pub fn expired(&self, now: Instant, timeout: Duration) -> bool {
        (!self.parked || !self.initialized) && now.saturating_duration_since(self.last) >= timeout
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn burst_rate_fraction_and_saturation() {
        let mut b = TokenBucket::new(FUEL_CAP, FUEL_RATE, FUEL_CAP, Duration::ZERO);
        assert_eq!(b.take(u64::MAX, Duration::ZERO), FUEL_CAP);
        assert_eq!(b.take(u64::MAX, Duration::from_nanos(5)), 0);
        assert_eq!(b.take(u64::MAX, Duration::from_nanos(10)), 1);
        assert_eq!(b.take(u64::MAX, Duration::from_secs(1)), 99_999_999);
        assert_eq!(b.take(u64::MAX, Duration::MAX), FUEL_CAP);
        assert_eq!(b.take(1, Duration::ZERO), 0);
    }
    #[test]
    fn initial_worker_burst_is_not_granted_twice_and_only_deficit_is_credited() {
        let mut b = FuelBudget::new(FUEL_CAP);
        assert_eq!(b.grant(0, Duration::ZERO), Ok(0));
        assert_eq!(b.grant(FUEL_CAP - 7, Duration::from_secs(1)), Ok(7));
        assert_eq!(b.grant(0, Duration::from_secs(1)), Ok(FUEL_RATE - 7));
        assert_eq!(b.grant(0, Duration::from_secs(100)), Ok(FUEL_CAP));
        assert!(b.grant(FUEL_CAP + 1, Duration::from_secs(100)).is_err());
        b.total = u64::MAX;
        assert!(b.grant(0, Duration::from_secs(101)).is_err());
    }
    #[test]
    fn worker_rejects_bounded_missing_excessive_and_overflow_credit() {
        let mut bounded = WorkerFuel::new(7, None).unwrap();
        assert!(bounded.apply(3, Some(1)).is_err());
        assert!(WorkerFuel::new(7, Some(8)).is_err());
        let mut w = WorkerFuel::new(7, Some(7)).unwrap();
        assert!(w.apply(1, None).is_err());
        assert!(w.apply(1, Some(u64::MAX)).is_err());
        assert!(w.apply(7, Some(1)).is_err());
        assert_eq!(w.apply(3, Some(4)), Ok(7));
        assert_eq!(w.total, 11);
        w.total = u64::MAX;
        assert!(w.apply(0, Some(1)).is_err());
    }
    #[test]
    fn request_window_is_rolling_and_bounded() {
        let mut r = RequestBudget::new(2);
        assert!(r.accept(Duration::ZERO).is_ok());
        assert!(r.accept(Duration::from_secs(30)).is_ok());
        assert!(r.accept(Duration::from_secs(59)).is_err());
        assert!(r.accept(Duration::from_secs(60)).is_ok());
        assert!(r.accept(Duration::from_secs(60)).is_err());
        assert!(r.accept(Duration::from_secs(90)).is_ok());
    }
    #[test]
    fn service_time_has_one_second_burst_and_half_second_per_second_rate() {
        let mut b = ServiceBudget::new();
        assert_eq!(
            b.charge(Duration::from_secs(1), Duration::ZERO),
            Duration::ZERO
        );
        assert_eq!(
            b.charge(Duration::from_millis(500), Duration::from_secs(1)),
            Duration::ZERO
        );
        assert_eq!(
            b.charge(Duration::from_millis(500), Duration::from_secs(1)),
            Duration::from_secs(1)
        );
        assert_eq!(
            b.charge(Duration::from_millis(500), Duration::from_secs(2)),
            Duration::from_secs(1)
        );
    }
    #[test]
    fn startup_is_bounded_and_only_initialized_parked_wait_exempts_progress_timeout() {
        let mut p = Progress::new();
        let later = p.last + Duration::from_secs(1);
        p.parked = true;
        assert!(p.expired(later, Duration::from_millis(1)));
        p.initialized = true;
        assert!(!p.expired(later, Duration::from_millis(1)));
        p.parked = false;
        assert!(p.expired(later, Duration::from_millis(1)));
    }
}
