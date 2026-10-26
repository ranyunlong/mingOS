//! Session-owned immutable shadow styles. No pixels, filesystem or GPU objects.
const FIRST: i32 = 0x4000_0001;
const LAST: i32 = 0x4fff_ffff;
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Style {
    pub radius: i32,
    pub blur: i32,
}
#[derive(Clone, Copy)]
struct Entry {
    handle: i32,
    style: Style,
}
pub(crate) struct Styles {
    entries: [Option<Entry>; 8],
    next: i32,
}
impl Default for Styles {
    fn default() -> Self {
        Self {
            entries: [None; 8],
            next: FIRST,
        }
    }
}
impl Styles {
    pub fn create(&mut self, radius: i32, blur: i32) -> i32 {
        if !(0..=64).contains(&radius) || !(0..=16).contains(&blur) {
            return -2;
        }
        if self.len() == self.entries.len() || !(FIRST..=LAST).contains(&self.next) {
            return -4;
        }
        let handle = self.next;
        let slot = self
            .entries
            .iter_mut()
            .find(|entry| entry.is_none())
            .expect("checked style slot");
        *slot = Some(Entry {
            handle,
            style: Style { radius, blur },
        });
        self.next += 1;
        handle
    }
    pub fn get(&self, handle: i32) -> Option<Style> {
        self.entries
            .iter()
            .flatten()
            .find(|entry| entry.handle == handle)
            .map(|entry| entry.style)
    }
    pub fn close(&mut self, handle: i32) -> bool {
        if let Some(entry) = self
            .entries
            .iter_mut()
            .find(|entry| entry.as_ref().is_some_and(|e| e.handle == handle))
        {
            *entry = None;
            true
        } else {
            false
        }
    }
    pub fn len(&self) -> usize {
        self.entries.iter().flatten().count()
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn exactly_eight_styles_then_close_admits_new_monotonic_handle() {
        let mut styles = Styles::default();
        let mut handles = Vec::new();
        for n in 0..8 {
            let h = styles.create(n, 16 - n);
            assert_eq!(h, FIRST + n);
            handles.push(h);
        }
        assert_eq!(styles.len(), 8);
        assert_eq!(styles.create(1, 1), -4);
        assert!(styles.close(handles[3]));
        assert_eq!(styles.get(handles[3]), None);
        let fresh = styles.create(64, 16);
        assert_eq!(fresh, FIRST + 8);
        assert_eq!(
            styles.get(fresh),
            Some(Style {
                radius: 64,
                blur: 16
            })
        );
        assert_eq!(styles.len(), 8);
    }
    #[test]
    fn invalid_style_does_not_consume_slot_or_id() {
        let mut styles = Styles::default();
        for (r, b) in [(-1, 0), (65, 0), (0, -1), (0, 17), (i32::MAX, i32::MAX)] {
            assert_eq!(styles.create(r, b), -2);
            assert_eq!(styles.len(), 0);
            assert_eq!(styles.next, FIRST);
        }
        assert_eq!(styles.create(0, 0), FIRST);
    }
    #[test]
    fn invalid_and_other_resource_handles_never_resolve() {
        let mut styles = Styles::default();
        let h = styles.create(4, 2);
        assert_eq!(h, FIRST);
        for bad in [i32::MIN, -1, 0, 1, 0x3000_0001, FIRST + 1, LAST] {
            assert!(styles.get(bad).is_none());
            assert!(!styles.close(bad));
        }
        assert_eq!(styles.get(h), Some(Style { radius: 4, blur: 2 }));
    }
    #[test]
    fn close_is_explicit_and_values_copied_before_close_stay_immutable() {
        let mut styles = Styles::default();
        let h = styles.create(64, 16);
        let saved = styles.get(h).unwrap();
        assert!(styles.close(h));
        assert!(!styles.close(h));
        assert_eq!(styles.get(h), None);
        assert_eq!(
            saved,
            Style {
                radius: 64,
                blur: 16
            }
        );
        assert_eq!(styles.len(), 0);
    }
    #[test]
    fn same_numbers_in_different_sessions_resolve_only_local_styles() {
        let mut a = Styles::default();
        let mut b = Styles::default();
        let ha = a.create(1, 2);
        let hb = b.create(3, 4);
        assert_eq!(ha, hb);
        assert_eq!(a.get(hb), Some(Style { radius: 1, blur: 2 }));
        assert_eq!(b.get(ha), Some(Style { radius: 3, blur: 4 }));
        assert!(a.close(ha));
        assert_eq!(b.get(hb), Some(Style { radius: 3, blur: 4 }));
    }
    #[test]
    fn handle_exhaustion_is_a_quota_and_cannot_wrap_or_reuse() {
        let mut styles = Styles {
            next: LAST,
            ..Styles::default()
        };
        assert_eq!(styles.create(0, 0), LAST);
        assert!(styles.close(LAST));
        assert_eq!(styles.create(0, 0), -4);
        assert_eq!(styles.len(), 0);
    }
    #[test]
    fn fixed_metadata_does_not_allocate_a_growing_table() {
        assert!(std::mem::size_of::<Styles>() <= 1024);
    }
}
