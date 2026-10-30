// Native smoke test, compiled directly with rustc on an isolated Windows runner.
// Exercises the production implementation, not a PowerShell reimplementation.
#[path = "../../dyauthreply-client/desktop/src-tauri/src/windows_input.rs"]
mod windows_input;
use std::ffi::c_void;
type Handle = *mut c_void;
#[repr(C)]
#[derive(Default)]
struct Message {
    window: Handle,
    message: u32,
    wparam: usize,
    lparam: isize,
    time: u32,
    point: [i32; 2],
    private: u32,
}
#[link(name = "user32")]
extern "system" {
    fn CreateWindowExW(
        ex: u32,
        class: *const u16,
        name: *const u16,
        style: u32,
        x: i32,
        y: i32,
        width: i32,
        height: i32,
        parent: Handle,
        menu: Handle,
        instance: Handle,
        param: Handle,
    ) -> Handle;
    fn SetForegroundWindow(window: Handle) -> i32;
    fn GetForegroundWindow() -> Handle;
    fn SetFocus(window: Handle) -> Handle;
    fn DestroyWindow(window: Handle) -> i32;
    fn LoadKeyboardLayoutW(name: *const u16, flags: u32) -> Handle;
    fn ActivateKeyboardLayout(layout: Handle, flags: u32) -> Handle;
    fn GetKeyboardLayout(thread: u32) -> Handle;
    fn GetKeyboardLayoutNameW(name: *mut u16) -> i32;
    fn SystemParametersInfoW(action: u32, param: u32, out: *mut c_void, flags: u32) -> i32;
    fn PeekMessageW(message: *mut Message, window: Handle, min: u32, max: u32, flags: u32) -> i32;
    fn TranslateMessage(message: *const Message) -> i32;
    fn DispatchMessageW(message: *const Message) -> isize;
}
#[link(name = "imm32")]
extern "system" {
    fn ImmIsIME(layout: Handle) -> i32;
}
fn wait_confirmed(pending: &windows_input::Pending) {
    // Production returns to Tauri's message loop while waiting for the posted
    // language request. This standalone test must also dispatch those messages.
    for _ in 0..20 {
        unsafe {
            let mut message = Message::default();
            while PeekMessageW(&mut message, std::ptr::null_mut(), 0, 0, 1) != 0 {
                TranslateMessage(&message);
                DispatchMessageW(&message);
            }
        }
        if windows_input::confirmed(pending).unwrap() {
            return;
        }
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    unsafe {
        let layout = GetKeyboardLayout(0);
        panic!(
            "Input confirmation failed: HKL={:x}, ImmIsIME={}",
            layout as usize,
            ImmIsIME(layout)
        );
    }
}
fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(Some(0)).collect()
}
struct Window(Handle, Handle);
impl Drop for Window {
    fn drop(&mut self) {
        unsafe {
            ActivateKeyboardLayout(self.1, 0);
            DestroyWindow(self.0);
        }
    }
}
fn main() {
    unsafe {
        let original = GetKeyboardLayout(0);
        let mut default_before: Handle = std::ptr::null_mut();
        assert_ne!(
            SystemParametersInfoW(0x59, 0, &mut default_before as *mut _ as _, 0),
            0
        );
        let window = CreateWindowExW(
            0,
            wide("STATIC").as_ptr(),
            wide("Input preflight test").as_ptr(),
            0x10CF0000,
            100,
            100,
            400,
            200,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
        );
        assert!(!window.is_null());
        let _cleanup = Window(window, original);
        SetForegroundWindow(window);
        SetFocus(window);
        assert_eq!(
            GetForegroundWindow(),
            window,
            "Runner needs an interactive desktop"
        );
        let french = LoadKeyboardLayoutW(wide("0000040C").as_ptr(), 1);
        assert!(!french.is_null());
        assert!(!ActivateKeyboardLayout(french, 0).is_null());
        let mut name = [0u16; 9];
        assert_ne!(GetKeyboardLayoutNameW(name.as_mut_ptr()), 0);
        assert_eq!(&name[..8], &wide("0000040C")[..8]);
        println!("BASELINE_INPUT=0000040C");
        let pending = windows_input::begin(window as usize).expect("activate US keyboard");
        wait_confirmed(&pending);
        assert_ne!(GetKeyboardLayoutNameW(name.as_mut_ptr()), 0);
        assert_eq!(&name[..8], &wide("00000409")[..8]);
        println!("MODIFIED_INPUT=00000409 CONFIRMED=true");
        // A manually chosen regional English layout must remain untouched.
        let english_uk = LoadKeyboardLayoutW(wide("00000809").as_ptr(), 1);
        assert!(!english_uk.is_null());
        assert!(!ActivateKeyboardLayout(english_uk, 0).is_null());
        let manual = GetKeyboardLayout(0);
        let prepared = windows_input::begin(window as usize).unwrap();
        wait_confirmed(&prepared);
        assert_eq!(GetKeyboardLayout(0), manual);
        println!("MANUAL_ENGLISH_PRESERVED=true");
        // Repeated starts are idempotent, and an unrelated/background window is rejected.
        wait_confirmed(&windows_input::begin(window as usize).unwrap());
        let before_reject = GetKeyboardLayout(0);
        assert!(windows_input::begin(0).is_err());
        assert_eq!(GetKeyboardLayout(0), before_reject);
        let handle = window as usize;
        assert!(std::thread::spawn(move || windows_input::begin(handle))
            .join()
            .unwrap()
            .is_err());
        let mut default_after: Handle = std::ptr::null_mut();
        assert_ne!(
            SystemParametersInfoW(0x59, 0, &mut default_after as *mut _ as _, 0),
            0
        );
        assert_eq!(
            default_before, default_after,
            "System default language was modified"
        );
        assert!(!ActivateKeyboardLayout(french, 0).is_null());
        assert!(!windows_input::confirmed(&pending).unwrap());
        println!("ROLLBACK_INPUT=0000040C CONFIRMED=false");
        wait_confirmed(&windows_input::begin(window as usize).unwrap());
        println!("WINDOWS_INPUT_PREFLIGHT_PASS=1 DEFAULT_UNCHANGED=true");
    }
}
