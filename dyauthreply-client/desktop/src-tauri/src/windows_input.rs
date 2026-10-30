//! Win32 input-layout preflight. No registry writes, default-language changes,
//! simulated hotkeys, IME removal or continuous input-layout enforcement.
//! Kept independent of Tauri so the exact code can be exercised on Windows CI.
use std::ffi::c_void;

type Handle = *mut c_void;
const US_KLID: [u16; 9] = [48, 48, 48, 48, 48, 52, 48, 57, 0]; // 00000409
const WM_INPUTLANGCHANGEREQUEST: u32 = 0x0050;
const KLF_ACTIVATE: u32 = 1;

#[repr(C)]
#[derive(Default)]
struct GuiThreadInfo {
    size: u32,
    flags: u32,
    active: Handle,
    focus: Handle,
    capture: Handle,
    menu_owner: Handle,
    move_size: Handle,
    caret: Handle,
    rect: [i32; 4],
}

#[link(name = "user32")]
extern "system" {
    fn GetForegroundWindow() -> Handle;
    fn GetWindowThreadProcessId(hwnd: Handle, process: *mut u32) -> u32;
    fn GetKeyboardLayout(thread: u32) -> Handle;
    fn GetKeyboardLayoutList(count: i32, layouts: *mut Handle) -> i32;
    fn GetGUIThreadInfo(thread: u32, info: *mut GuiThreadInfo) -> i32;
    fn IsChild(parent: Handle, child: Handle) -> i32;
    fn LoadKeyboardLayoutW(name: *const u16, flags: u32) -> Handle;
    fn ActivateKeyboardLayout(layout: Handle, flags: u32) -> Handle;
    fn PostMessageW(hwnd: Handle, message: u32, wparam: usize, lparam: isize) -> i32;
}

#[link(name = "kernel32")]
extern "system" {
    fn GetCurrentThreadId() -> u32;
}

#[derive(Debug)]
pub struct Pending {
    window: usize,
    focus: usize,
    window_thread: u32,
    focus_thread: u32,
}

fn error(operation: &str) -> String {
    let error = std::io::Error::last_os_error();
    let detail = if error.raw_os_error() == Some(0) {
        "系统未提供具体错误码".to_string()
    } else {
        error.to_string()
    };
    format!("英文键盘准备未确认（{operation}，{detail}）；仍将尝试打开浏览器")
}

fn focus_error() -> String {
    "客户端输入焦点已变化，跳过自动切换，仍将尝试打开浏览器".into()
}

fn english_language(layout: usize) -> bool {
    layout != 0 && (layout & 0x3ff) == 0x09
}

fn english_keyboard(layout: Handle) -> bool {
    // Windows CI reports ImmIsIME(0x04090409) == TRUE for the standard US
    // layout under TSF. Do not use that legacy classification as a gate.
    // Sogou's English typing mode still has a Chinese LANGID, unlike ENG.
    english_language(layout as usize)
}

pub fn begin(window: usize) -> Result<Pending, String> {
    // SAFETY: handles come from Tauri's live main window (or the native test
    // window); every Win32 result is checked. No pointers outlive their buffers.
    unsafe {
        let hwnd = window as Handle;
        if hwnd.is_null() || GetForegroundWindow() != hwnd {
            return Err(focus_error());
        }
        let window_thread = GetWindowThreadProcessId(hwnd, std::ptr::null_mut());
        if window_thread != GetCurrentThreadId() {
            return Err("英文键盘准备必须在客户端界面线程执行，请重新打开登录窗口".into());
        }
        let mut info = GuiThreadInfo {
            size: std::mem::size_of::<GuiThreadInfo>() as u32,
            ..Default::default()
        };
        if GetGUIThreadInfo(window_thread, &mut info) == 0 {
            return Err(error("读取焦点"));
        }
        let focus = if info.focus.is_null() {
            hwnd
        } else {
            info.focus
        };
        if focus != hwnd && IsChild(hwnd, focus) == 0 {
            return Err(focus_error());
        }
        let focus_thread = GetWindowThreadProcessId(focus, std::ptr::null_mut());
        if window_thread == 0 || focus_thread == 0 {
            return Err(error("读取窗口线程"));
        }
        let pending = Pending {
            window,
            focus: focus as usize,
            window_thread,
            focus_thread,
        };
        // Respect a manual ENG choice. Do not reactivate or name-check another
        // thread's layout after the user has already selected English.
        if english_keyboard(GetKeyboardLayout(focus_thread)) {
            return Ok(pending);
        }
        // Windows 8+ may activate the current system input language here.
        // Intentionally omit REORDER, SETFORPROCESS, SUBSTITUTE and any SPI
        // default-language mutation. Keep English for browser startup; restoring
        // Sogou immediately can reproduce the failure while Chrome initializes.
        if GetForegroundWindow() != hwnd {
            return Err(focus_error());
        }
        // Reuse US if installed: WebView2 can own keyboard focus in another
        // process, in which case LoadKeyboardLayout may have no effect. The
        // focused window receives the same language request as the taskbar.
        let mut layouts = [std::ptr::null_mut(); 256];
        let count = GetKeyboardLayoutList(layouts.len() as i32, layouts.as_mut_ptr());
        let existing = layouts[..(count.max(0) as usize).min(layouts.len())]
            .iter()
            .copied()
            .find(|layout| *layout as usize & 0xffff_ffff == 0x0409_0409);
        let layout =
            existing.unwrap_or_else(|| LoadKeyboardLayoutW(US_KLID.as_ptr(), KLF_ACTIVATE));
        if layout.is_null() || ActivateKeyboardLayout(layout, 0).is_null() {
            return Err(error("激活英文键盘"));
        }
        if GetForegroundWindow() != hwnd {
            return Err(focus_error());
        }
        if PostMessageW(focus, WM_INPUTLANGCHANGEREQUEST, 0, layout as isize) == 0 {
            return Err(error("通知输入焦点"));
        }
        Ok(pending)
    }
}

pub fn confirmed(pending: &Pending) -> Result<bool, String> {
    // SAFETY: query-only calls; also revalidate handles/threads to avoid using
    // an obsolete focus target if the user moves away during preparation.
    unsafe {
        let hwnd = pending.window as Handle;
        if GetForegroundWindow() != hwnd {
            return Err(focus_error());
        }
        let mut info = GuiThreadInfo {
            size: std::mem::size_of::<GuiThreadInfo>() as u32,
            ..Default::default()
        };
        if GetGUIThreadInfo(pending.window_thread, &mut info) == 0 {
            return Err(error("再次读取焦点"));
        }
        let focus = if info.focus.is_null() {
            hwnd
        } else {
            info.focus
        };
        if focus as usize != pending.focus {
            return Err(focus_error());
        }
        if GetWindowThreadProcessId(hwnd, std::ptr::null_mut()) != pending.window_thread
            || GetWindowThreadProcessId(pending.focus as Handle, std::ptr::null_mut())
                != pending.focus_thread
        {
            return Err(focus_error());
        }
        Ok(english_keyboard(GetKeyboardLayout(pending.focus_thread)))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_missing_or_background_window_without_changing_layout() {
        unsafe {
            let before = GetKeyboardLayout(0);
            assert!(begin(0).is_err());
            assert_eq!(GetKeyboardLayout(0), before);
        }
    }

    #[test]
    fn us_klid_is_a_keyboard_not_sogou_english_mode() {
        assert_eq!(String::from_utf16(&US_KLID[..8]).unwrap(), "00000409");
    }

    #[test]
    fn english_layout_handles_need_not_have_one_exact_name_or_value() {
        for layout in [0x0409_0409, 0x0809_0809, 0xf123_0409] {
            assert!(english_language(layout));
        }
        for layout in [0, 0x0804_0804, 0xe020_0804, 0x040c_040c] {
            assert!(!english_language(layout));
        }
    }
}
