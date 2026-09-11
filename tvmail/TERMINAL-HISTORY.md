# The Great Terminal Divergence & Convergence: A History of Screen Addressing from Unix to Windows

For decades, the developer experience on Unix/Linux and MS-DOS/Windows was separated by a fundamental architectural divide: **how programs communicate with the terminal screen**. Unix built an ecosystem around character streams, teletypes, and ANSI/VT control sequences. Windows, by contrast, built its command-line interface around object handles, memory buffers, and programmatic Win32 API calls.

This document traces the history of how screen addressing and terminal control diverged between these OS paradigms, the legacy abstractions created on both sides, and the modern engineering efforts (from WinPty to Windows Terminal and ConPTY) that brought them back together — and the specific corner where, as of 2026, they still haven't: `tvmail` (this repo) running over SSH into a Windows Terminal client (§7).

---

## 1. The Unix Lineage: Character Streams & TTYs

```
+------------------+         +-------------------+         +--------------------+
| Application Process | <---> | Terminal Emulator | <---> | Physical Terminal  |
|  (e.g., vim, htop)  |       |   (e.g., xterm)   |       |   (e.g., VT100)    |
+------------------+         +-------------------+         +--------------------+
      |                             ^
      | Byte Stream (In/Out)        | In-band ANSI Control Sequences
      +-----------------------------+ (e.g., `\x1b[2J`, `\x1b[H`)
```

### 1.1 The Hardware Legacy (Teletypes & VT100)
The Unix terminal paradigm originated with physical hardware devices: electro-mechanical teletypes (TTYs) such as the Teletype Model 33, and later glass terminals like the **DEC VT52** and **DEC VT100** (1978).

Because physical terminals were connected to mainframe host computers via serial communication lines (e.g., RS-232), all interaction—both standard output text and control directives (cursor positioning, text coloring, screen clearing)—had to be multiplexed into a single **in-band byte stream**.

### 1.2 ANSI Escape Sequences
To standardize control signals, the standards body published **ANSI X3.64** (later adapted into **ECMA-48** and **ISO/IEC 6429**). These control directives rely on *Escape Sequences*: byte strings starting with the `ESC` character (`` or `0x1B`), often followed by `[` (forming the **Control Sequence Introducer**, or `CSI`).

* **Cursor Movement:** `[<line>;<col>H` or `[<line>;<col>f`
* **Clear Screen:** `[2J`
* **Text Attributes (SGR):** `[31;1m` (Set Red, Bold text)

### 1.3 `termcap`, `terminfo`, and `[n]curses`
Because different hardware terminals supported varying subsets of escape sequences (or non-standard codes), Unix developers required an abstraction layer to write portable, full-screen terminal software:

1. **`termcap` (Terminal Capability) (1978):** Created by Bill Joy for BSD Unix (and used by `vi`), `termcap` was a flat-file database (`/etc/termcap`) describing hardware terminal capabilities using short two-letter codes (e.g., `cm` for cursor motion, `cl` for clear screen).
2. **`terminfo` (1980s):** Developed by Pavel Curtis and Mary Ann Horton for System V Unix, `terminfo` replaced `termcap`. It compiled textual descriptions into faster binary database files (`/usr/share/terminfo/*`), providing richer capability flags and parameter evaluations.
3. **`curses` & `ncurses`:** Built atop `termcap`/`terminfo`, the **`curses`** library (and its open-source successor, **`ncurses`**, written by Zeyd Ben-Halim and Eric S. Raymond) provided a C API for windowing, memory-buffered drawing, line drawing, and mouse support on textual terminals. Programs like `htop`, `nano`, `vim`, and `tmux` rely on `ncurses` to construct complex text user interfaces (TUIs).

### 1.4 Graphical Terminal Emulators (`xterm`)
With the advent of graphical X Window Systems in the mid-1980s, software replaced hardware. **`xterm`** became the de facto standard terminal emulator. Beyond basic VT100/VT102 compatibility, `xterm` introduced standard extensions for 256-color support, mouse reporting (xterm tracking modes), and title bar Manipulation, which remain standard across modern terminal emulators today (GNOME Terminal, Alacritty, Kitty, iTerm2).

---

## 2. The DOS & Windows Lineage: Direct Buffers & API Handles

While Unix leaned into in-band stream parsing, MS-DOS and early Windows rejected in-band ANSI escape sequences in favor of **direct memory access** and **out-of-band programmatic APIs**.

```
+-----------------------+                    +-------------------------+
| Win32 Console App     |                    | ConHost.exe             |
| (e.g., cmd.exe, cl.exe)|                    | (Windows Console Subsys)|
+-----------------------+                    +-------------------------+
      |                                                   |
      | Win32 Console API Calls                           | Screen Buffer
      | (WriteConsoleOutput, SetConsoleCursorPosition)    | (Character & Attribute)
      +-------------------------------------------------->+
```

### 2.0 Before DOS: CP/M's In-Band Legacy (and Why MS-DOS Left It Behind)

Before treating DOS as the origin of the API/handle paradigm, it's worth noting DOS's own ancestor didn't work that way. **MS-DOS began life as 86-DOS**, nicknamed **QDOS** ("Quick and Dirty Operating System"), written by Tim Paterson at Seattle Computer Products and acquired by Microsoft in 1980. It was deliberately modeled on **CP/M** (Gary Kildall, Digital Research, 1974) — closely enough that CP/M's `BDOS` function-call numbers map almost one-to-one onto early DOS's `INT 21h` calls, and CP/M's `.COM` binary format carried straight through into DOS.

CP/M itself used **in-band character-stream console I/O**, not memory-mapped buffers or API handles — for the same reason Unix did: **hardware fragmentation**. CP/M ran across a zoo of mutually incompatible 8-bit machines (Kaypro, Osborne, Xerox 820, Morrow, TRS-80 in CP/M mode, and dozens more), each with its own video hardware and its own idea of a memory map, if it had memory-mapped video at all. There was no equivalent of the IBM PC's later `0xB8000` to write to directly. So CP/M's `BDOS` console-output calls (function 2 / function 9) just wrote raw bytes — text and cursor/attribute control codes alike — down a logical stream to a thin, OEM-supplied `BIOS` jump table: the same layering Unix used (`ncurses` → `terminfo` → raw bytes → hardware), just without a shared standards body behind it.

The catch: CP/M never standardized *which* control codes meant what. An ADM-3A used one cursor-addressing scheme, a Heath/Zenith H19 another, a Kaypro's built-in screen a third. Instead of a shared capability database like `termcap`, full-screen CP/M software (WordStar being the classic example) shipped with an `INSTALL.COM`-style program that patched the right control codes directly into the *application binary* for your terminal — portability solved per-program, not per-system.

**The irony that sets up the rest of this document:** when the IBM PC standardized the clone market around one video architecture, DOS software didn't inherit CP/M's in-band model — it *abandoned* it, because direct memory/BIOS access was now cheap, fast, and (for the first time) safe to assume across nearly all target machines. The API/handle model in §2.2–2.3 below isn't a Windows-native idea so much as what you get when hardware fragmentation is solved by market consolidation instead of by a software abstraction layer. Decades later, virtualization and remote sessions reintroduced exactly the fragmentation problem CP/M and Unix solved with in-band streams — which is a large part of why Windows eventually had to grow one too (§4). And as §7 shows, that regrowth is still incomplete at the edges — which is exactly how this repo's SSH corruption bug happened.

### 2.1 MS-DOS: BIOS Calls and Video RAM
In the MS-DOS world, application developers bypass standard streams (`stdout`) when building rich text applications:
* **BIOS Int 10h:** Called directly to set cursor position or write character attributes.
* **Direct Video RAM Write:** To achieve fast redraws, DOS programs wrote directly to memory locations `0xB8000` (Color Text Buffer) or `0xB000` (Monochrome Buffer). Screen cells were structured as 2-byte tuples: `[1 Byte ASCII Character | 1 Byte Attribute (4-bit foreground, 4-bit background)]`.

Microsoft provided an optional driver, `ANSI.SYS`, which parsed escape sequences passed through `STDOUT`, but it was notoriously slow, memory-intensive, disabled by default, and bypassed by almost all commercial software (e.g., WordPerfect, Lotus 1-2-3, Borland Turbo C).

**And then even that was taken away.** `ANSI.SYS`'s escape-code interpretation only ever ran inside the MS-DOS/Win16 environment. On NT-based Windows (NT4 through 7/8.1) it survived solely inside the 16-bit **`NTVDM`** subsystem via `CONFIG.NT` — still invisible to native 32-bit console apps, which had no escape-code interpretation of any kind. When 64-bit Windows dropped `NTVDM` entirely (there's no virtual‑8086 mode in long mode to host it — gone from XP x64 in 2005 onward, and with it on every 64‑bit SKU since), that last foothold disappeared too. From then until Windows 10 added `ENABLE_VIRTUAL_TERMINAL_PROCESSING` in 2016 (§4.1), there was **no in-band ANSI interpretation anywhere in native Windows** — not even the old DOS-box version. Developers who wanted colored console output in the meantime reached for third-party hacks like Jason Hood's **`ANSICON`** (2005), which hooked `WriteConsole`/`WriteFile` to reimplement `ANSI.SYS`-style translation for 32-bit apps — filling, unofficially, a gap Microsoft itself didn't close for over a decade.

### 2.2 Windows NT and `ConHost`
When Microsoft introduced Windows NT (1993), direct hardware/memory access was banned in user mode for safety and security. To support command-line programs, Microsoft created the **Win32 Console Subsystem**.

Rather than implementing a stream-based terminal emulator, Windows introduced a rich **Win32 Console API**:
* `GetStdHandle(STD_OUTPUT_HANDLE)`
* `SetConsoleCursorPosition(...)`
* `WriteConsoleOutputAttribute(...)`
* `ReadConsoleInput(...)` (structured event structs, not raw character stream inputs)

Under the hood, Windows managed a server process:
* **`csrss.exe`** (Client/Server Runtime Subsystem) in early Windows NT versions.
* **`conhost.exe`** (Console Window Host), introduced in Windows 7 to isolate console hosting out of `csrss.exe` for security, stability, and visual styling (DWM/Aero support).

### 2.3 The Architectural Split
This generated a fundamental split between paradigms:

| Feature | Unix / POSIX Paradigm | Windows Console Paradigm |
| :--- | :--- | :--- |
| **Addressing Model** | In-band ANSI/VT control sequence stream | Out-of-band procedural C API calls |
| **Buffer Management** | Managed in-process by software (`ncurses`) | Managed out-of-process by OS Kernel/ConHost |
| **Input Structure** | Raw byte stream (e.g., `[A` for Up Arrow) | Native `INPUT_RECORD` structs (KeyEvent, MouseEvent) |
| **Redirection/Piping** | Pipes carry characters + escape sequences seamlessly | Pipes stripped formatting; Console APIs fail over non-console handles |

---

## 3. The Collision: Cross-Platform Tools and Shims

As open-source cross-platform software expanded, the incompatibility between Unix stream-based control and Windows API-based control created severe interoperability bugs:
* Unix utilities running on Windows could not color text or clear the screen because `WriteFile(stdout, "[2J")` printed literal `^[2J` gibberish.
* Windows terminal emulators could not easily host interactive Unix shells via SSH/Cygwin/MSYS2 without complex translations.

To solve this, developer communities engineered several intermediate translation layers.

### 3.1 Cygwin, MSYS2, and `WinPty`
Environments like Cygwin and MSYS2 compiled Unix programs against Windows. However, when a POSIX program expected a pseudo-terminal (PTY) standard stream, connecting it to Windows `ConHost` caused console handles to fail (`GetConsoleMode` returns invalid handle errors).

To bridge this, **`WinPty`** (created by Ryan Prichard) was created:
1. `WinPty` spawns a hidden background `conhost.exe` window.
2. It interacts with the target Windows application using native Win32 Console APIs.
3. It periodically **scrapes the hidden screen buffer** (`ReadConsoleOutputCharacter`), diffs changes, converts those changes into ANSI/VT100 control sequences, and streams them out over a UNIX-style PTY.
4. Conversely, incoming ANSI sequences were translated into native Windows input events (`WriteConsoleInput`).

While effective, `WinPty` incurred heavy performance overhead due to screen-buffer polling/scraping and had edge-case bugs with complex Unicode or fast-scrolling text.

### 3.2 ConEmu & Clink
Third-party graphical terminal hosts like **ConEmu** (by Maximus5) attempted to provide a modern, tabbed, customizable terminal UI for Windows.

Because Windows offered no public API to embed or replace `conhost.exe`, ConEmu had to perform complex process injection ("hooking") into console apps or host `conhost.exe` as a child window, intercepting API calls to render rich graphical interfaces, ANSI colors, and split panes.

---

## 4. The Grand Convergence: Native Virtual Terminal Sequences & ConPTY

Recognizing that the Unix stream-based VT sequence model had effectively won the global standard for remote access, web terminals, and cross-platform tooling, Microsoft embarked on a multi-year project to modernize the Windows command-line architecture.

```
+-------------------------------------------------------------------------------+
|                             Windows Terminal App                              |
|         (Renders screen using DirectX / TextRenderer & parses VT Streams)     |
+-------------------------------------------------------------------------------+
                                       ^
                                       | VT Sequence Input/Output Stream
                                       v
+-------------------------------------------------------------------------------+
|                       ConPTY Engine (conhost.exe module)                      |
|                                                                               |
|  +---------------------------+             +-------------------------------+  |
|  | Native Win32 Console APIs | <---------> | VT Processing Engine          |  |
|  | (WriteConsoleOutput, etc.)|             | (Parses & Emits ANSI Sequences|  |
|  +---------------------------+             +-------------------------------+  |
+-------------------------------------------------------------------------------+
                                       ^
                                       | Bidirectional Translation
                                       v
+-------------------------------------------------------------------------------+
| Target Process (cmd.exe, powershell.exe, wsl.exe, node.exe, gdb, vim)         |
+-------------------------------------------------------------------------------+
```

### 4.1 Native VT Parsing in Windows 10 (2016)
Starting with Windows 10 (TH2 / Version 1511), Microsoft added a native **Virtual Terminal (VT) Processing Engine** inside `conhost.exe`. Developers could pass a flag to the console API:

```c
DWORD mode;
GetConsoleMode(hOut, &mode);
SetConsoleMode(hOut, mode | ENABLE_VIRTUAL_TERMINAL_PROCESSING);
```

Once enabled, Windows `conhost.exe` intercepts byte streams written via standard `WriteFile()` or `printf()`, parses standard VT100/ANSI/xterm escape sequences, and updates internal screen buffers automatically. Windows natively understood `[31m` without requiring `SetConsoleTextAttribute()`.

### 4.2 The Windows Pseudo Console (`ConPTY`) (2018)
While native VT parsing helped console applications, third-party terminal apps (like VS Code's integrated terminal, Alacritty, or Hyper) still struggled to host legacy Windows apps.

In 2018, Microsoft released the **ConPTY (Windows Pseudo Console) API**:
* `CreatePseudoConsole()`

`ConPTY` acts as a full-fledged software PTY infrastructure built directly into the OS kernel/subsystem runtime.
* **For modern apps:** It provides a raw ANSI/VT bidirectional stream over standard pipes (`HANDLE hInput, HANDLE hOutput`).
* **For legacy Win32 apps:** If a legacy application calls `SetConsoleCursorPosition()`, `ConPTY` catches the call, updates its internal buffer state, and outputs the equivalent VT escape sequence (`[y;xH`) out to the connected terminal interface.

`ConPTY` rendered legacy hacks like `WinPty` and DLL hooking obsolete, creating a true parity bridge between POSIX and Windows terminal abstractions.

### 4.3 Windows Terminal
Armed with `ConPTY`, Microsoft developed **Windows Terminal**—an open-source, modern, GPU-accelerated (DirectWrite/DirectX) terminal emulator.

Windows Terminal fully treats both Windows Native environments (PowerShell, CMD) and Linux environments (WSL - Windows Subsystem for Linux, SSH) as equal citizens. Everything communicates through standard VT/ANSI sequence streams over `ConPTY`.

---

## 5. Architectural Comparison Matrix

| Technology | Era | Paradigm | Primary API / Protocol | Legacy / Current Status |
| :--- | :--- | :--- | :--- | :--- |
| **VT100** | 1978 | Hardware Stream | Serial ANSI Control Sequences | Historical ancestor of modern terminals |
| **CP/M `BDOS` Console I/O** | 1974 | In-band Stream (fragmented HW) | Raw byte stream via OEM `BIOS` jump table | Historical; direct ancestor of MS-DOS's `INT 21h` API layer |
| **`termcap` / `terminfo`** | 1978/1980s | Abstraction Database | Text/Binary capability definitions | Active standard for POSIX capability lookup |
| **`ncurses`** | 1993 | Screen Buffer API | C Functions (`mvprintw`, `waddch`) | Active standard for Unix TUIs |
| **MS-DOS / BIOS** | 1981 | Hardware Memory | Direct Int 10h / `0xB800` RAM access | Obsolete |
| **Win32 Console (`conhost`)** | 1993 | OS Handle / Out-of-band | Win32 Console APIs (`WriteConsoleOutput`) | Maintained for backward compatibility |
| **`WinPty`** | 2011 | Buffer Scraping Shim | Scraping Win32 buffers to VT streams | Deprecated (Replaced by ConPTY) |
| **`ConPTY`** | 2018 | Native OS PTY | Virtual Terminal Byte Streams | Modern Windows Standard |
| **Windows Terminal** | 2019+ | Graphical Terminal Host | GPU-rendered VT Byte Streams | Active modern default on Windows 11 |

---

## 6. Summary: The Modern Landscape

Today, the divergence has effectively closed:

1. **ANSI/VT Control Sequences** are the universal, standard IPC language for rich interactive text formatting across Linux, macOS, and Windows.
2. **`ConPTY`** allows Windows to expose POSIX-style PTY pipes to software while maintaining compatibility with legacy apps that call Win32 Console APIs.
3. **Cross-platform TUI libraries** (e.g., `Crossterm`, `FTXUI`, `Bubbletea`, `Rich`/`Textual`, `Inquirer.js`) write universal code targeting ANSI streams, running identically in `xterm`, GNOME Terminal, iTerm2, and Windows Terminal.
4. **With one asterisk:** the above holds firmly for local, single-hop console apps. It gets measurably shakier for remote, full-screen TUIs that negotiate terminal capabilities live over a connection like SSH — see §7.

---

## 7. The Asterisk: Where the Convergence Still Leaks

§6 above isn't wrong, but it's told from the vantage point of a console app one hop from `ConPTY` — `pwsh.exe`, `vim` under WSL, `node.exe`. That's the case `ConPTY` was built for, and it works well. There's a narrower case it doesn't fully solve: **a full-screen TUI, tunneled over SSH, that does its own out-of-band negotiation with the terminal.** `tvmail` is exactly that case.

### 7.1 Why negotiation breaks the model

Apps like `vim` or `htop` mostly *write* — SGR colors, cursor moves, screen clears. `ConPTY` can translate that all day without knowing or caring who's on the other end.

A framework like [magiblot/tvision](https://github.com/magiblot/tvision) (the engine behind `tvmail`) also *asks questions*: Primary Device Attributes (`ESC[c`), window-size reports (`ESC[18t`), pixel geometry, mouse-encoding capability, `kitty`/`modifyOtherKeys` support. It expects the answers to come back byte-for-byte from the real terminal it's negotiating with. When that terminal is a hop away over SSH, `ConPTY` (or Cygwin's own console bridge, which has the same job one layer down) sits *in the middle of that conversation* — and because it has to keep its internal console-buffer model in sync for legacy Win32 apps, it can't just relay the bytes. It intercepts, reframes, or answers on the app's behalf. The reply that lands back at the remote TUI is no longer the one the real terminal would have sent.

### 7.2 Case study: `tvmail` over SSH from Cygwin

Confirmed empirically, September 2026, running this repo's `tvmail` on a Raspberry Pi over SSH from Windows:

| Client / terminal | Result |
| :--- | :--- |
| Cygwin `ssh` in Windows Terminal, `pcon` default | Mangled query replies leak onto screen as garbage (`M3m3M3m`, `[8;14;…`) |
| Cygwin `ssh` in Windows Terminal, `CYGWIN=disable_pcon` | No more garbage — but keystrokes and mouse clicks are silently dropped |
| Cygwin `ssh` in **mintty** | Clean: no garbage, full input fidelity |

The same failure class hits the *native* Windows OpenSSH client too — see [Win32-OpenSSH#2275](https://github.com/PowerShell/Win32-OpenSSH/issues/2275), where escape sequences leak into a `tmux` session and the only documented workaround (`SSH_TERM_CONHOST_PARSER=0`) trades garbage for slow, glitchy rendering. It isn't a Cygwin-specific bug — it's `ConPTY`-family behavior, wherever it sits in the chain.

`mintty` sidesteps the problem entirely by not routing through `conhost`/`ConPTY` at all: it's a native Win32 GUI app that speaks VT directly to the Cygwin pty, so nothing between `tvmail` and the terminal is reinterpreting the stream. **This is why the recommended way to run `tvmail` over SSH from a Windows/Cygwin client is via `mintty`, not Windows Terminal.**

### 7.3 The corrected claim

> `ConPTY` closes the gap for **local, single-hop console apps** — the WSL/PowerShell/VS Code terminal case §4–§6 describe. It does not yet offer a transparent tunnel for **remote TUIs that negotiate terminal capabilities live**, because the very translation that makes legacy Win32 apps work is what corrupts that negotiation. For that case, as of 2026, an actual terminal emulator talking to an actual pty — `mintty`, a real `xterm`, WSL's own console — remains the only fully lossless path.
