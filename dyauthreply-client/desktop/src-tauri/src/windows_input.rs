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
    fn GetKeyboardLayoutNameW(name: *mut u16) -> i32;
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
    layout: usize,
    window_thread: u32,
    focus_thread: u32,
}

fn error(operation: &str) -> String {
    format!(
        "登录前切换英文键盘失败（{operation}，{}）；请在任务栏选择 ENG（英语·美国）后重试，无需重装浏览器",
        std::io::Error::last_os_error()
    )
}

fn focus_error() -> String {
    "请先回到 D助手窗口，再点击重新打开登录窗口；为避免干扰其他应用，本次未启动浏览器".into()
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
        // Windows 8+ may activate the current system input language here.
        // Intentionally omit REORDER, SETFORPROCESS, SUBSTITUTE and any SPI
        // default-language mutation. Keep English for browser startup; restoring
        // Sogou immediately can reproduce the failure while Chrome initializes.
        if GetForegroundWindow() != hwnd {
            return Err(focus_error());
        }
        let layout = LoadKeyboardLayoutW(US_KLID.as_ptr(), KLF_ACTIVATE);
        if layout.is_null() || ActivateKeyboardLayout(layout, 0).is_null() {
            return Err(error("激活英文键盘"));
        }
        let mut name = [0u16; 9];
        if GetKeyboardLayoutNameW(name.as_mut_ptr()) == 0 || name != US_KLID {
            return Err(error("校验英文键盘"));
        }
        if GetForegroundWindow() != hwnd {
            return Err(focus_error());
        }
        if PostMessageW(focus, WM_INPUTLANGCHANGEREQUEST, 0, layout as isize) == 0 {
            return Err(error("通知输入焦点"));
        }
        Ok(Pending {
            window,
            focus: focus as usize,
            layout: layout as usize,
            window_thread,
            focus_thread,
        })
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
        Ok(
            GetKeyboardLayout(pending.window_thread) as usize == pending.layout
                && GetKeyboardLayout(pending.focus_thread) as usize == pending.layout,
        )
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
}
