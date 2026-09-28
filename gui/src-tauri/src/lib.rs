//! The shell. It owns the pipes and nothing else.
//!
//! `aven --mode rpc` is spawned as a sidecar and outlives every command sent to
//! it, because the things a person is about to approve live in that process's
//! memory: a staged call carries an unfired closure over real paths and real
//! functions, which cannot be serialised and cannot outlive the process holding
//! it. Restarting the agent between prompts would throw away the tray, and the
//! tray is what the window exists to show.
//!
//! So there are exactly two jobs here. Write a line into stdin. Read lines out
//! of stdout and hand each one to the webview. No protocol knowledge, no
//! parsing, no opinion about what any of it means - that is all in TypeScript,
//! where it can be changed without a Rust rebuild.

use std::sync::Mutex;

use tauri::{AppHandle, Emitter, Manager, State};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// The running agent, or nothing yet.
struct Agent(Mutex<Option<CommandChild>>);

/// Where the folder list lives, beside the other two files a person edits.
///
/// Read and written here rather than from the webview, on purpose. The window's
/// capability is spawning one named sidecar and nothing else; handing it
/// `fs:allow-write` on the home directory to save a list of two paths would
/// undo that for a convenience. Rust touches one known file instead.
///
/// TOML because `approvals.toml` and `triggers.toml` already are, and the point
/// of all three is that they can be read and edited without this window.
const CONFIG: &str = "desktop.toml";

fn config_path(app: &AppHandle) -> Result<std::path::PathBuf, String> {
    let home = app
        .path()
        .home_dir()
        .map_err(|problem| problem.to_string())?;
    Ok(home.join(".aven").join(CONFIG))
}

#[tauri::command]
fn roots_read(app: AppHandle) -> Result<Vec<String>, String> {
    let path = config_path(&app)?;
    let text = match std::fs::read_to_string(&path) {
        Ok(text) => text,
        // No file yet is not an error - it is a first run, and the window asks.
        Err(problem) if problem.kind() == std::io::ErrorKind::NotFound => {
            return Ok(vec![])
        }
        Err(problem) => return Err(problem.to_string()),
    };

    // A hand-edited file with a typo in it should not stop the window opening.
    // Nothing configured is the safe reading: it asks again rather than guessing.
    let parsed: toml::Value = match text.parse() {
        Ok(value) => value,
        Err(_) => return Ok(vec![]),
    };
    Ok(parsed
        .get("roots")
        .and_then(|roots| roots.as_array())
        .map(|roots| {
            roots
                .iter()
                .filter_map(|root| root.as_str().map(str::to_owned))
                .collect()
        })
        .unwrap_or_default())
}

#[tauri::command]
fn roots_write(app: AppHandle, roots: Vec<String>) -> Result<(), String> {
    let path = config_path(&app)?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|problem| problem.to_string())?;
    }

    // Written by hand rather than serialised, so the comment survives. Somebody
    // opening this file should be told what it is for and that they may edit it.
    let listed = roots
        .iter()
        .map(|root| format!("  {},\n", toml::Value::String(root.clone())))
        .collect::<String>();

    std::fs::write(
        &path,
        format!(
            "# Folders aven may act in, from the desktop window.\n\
             # The first one is the working folder: a bare path resolves against it.\n\
             # Yours to edit - the window reads this file and never argues with it.\n\
             roots = [\n{listed}]\n"
        ),
    )
    .map_err(|problem| problem.to_string())
}

#[tauri::command]
async fn agent_start(app: AppHandle, roots: Vec<String>) -> Result<(), String> {
    if app.state::<Agent>().0.lock().unwrap().is_some() {
        return Ok(()); // already up; starting twice would orphan the first one
    }

    let mut args: Vec<String> = vec!["--mode".into(), "rpc".into()];
    for root in roots {
        args.push("--root".into());
        args.push(root);
    }

    let (mut events, child) = app
        .shell()
        .sidecar("aven")
        .map_err(|problem| problem.to_string())?
        .args(args)
        .spawn()
        .map_err(|problem| problem.to_string())?;

    *app.state::<Agent>().0.lock().unwrap() = Some(child);

    let forwarding = app.clone();
    tauri::async_runtime::spawn(async move {
        // Split on LF and nothing else. A chunk from the pipe is an arbitrary
        // slice of bytes: one record can arrive in two chunks, and two records
        // can arrive in one. Anything that does not buffer to the newline itself
        // will eventually hand the webview half a JSON object.
        //
        // Deliberately not a line-reading helper. Some of them also break on
        // U+2028 and U+2029, which are legal inside a JSON string - and a
        // transcript in Chinese is exactly where those turn up.
        let mut spare: Vec<u8> = Vec::new();

        while let Some(event) = events.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => {
                    spare.extend_from_slice(&bytes);
                    while let Some(at) = spare.iter().position(|byte| *byte == b'\n') {
                        let line: Vec<u8> = spare.drain(..=at).collect();
                        let text = String::from_utf8_lossy(&line[..line.len() - 1]);
                        if !text.trim().is_empty() {
                            let _ = forwarding.emit("aven", text.to_string());
                        }
                    }
                }
                // stderr is commentary, never protocol. Worth seeing in a
                // terminal, never worth parsing.
                CommandEvent::Stderr(bytes) => {
                    eprint!("{}", String::from_utf8_lossy(&bytes));
                }
                CommandEvent::Terminated(status) => {
                    *forwarding.state::<Agent>().0.lock().unwrap() = None;
                    let _ = forwarding.emit("aven-gone", status.code);
                    break;
                }
                _ => {}
            }
        }
    });

    Ok(())
}

#[tauri::command]
fn agent_write(agent: State<Agent>, line: String) -> Result<(), String> {
    let mut held = agent.0.lock().unwrap();
    let child = held.as_mut().ok_or("the agent is not running")?;
    child
        .write(format!("{line}\n").as_bytes())
        .map_err(|problem| problem.to_string())
}

#[tauri::command]
fn agent_stop(agent: State<Agent>) -> Result<(), String> {
    // Dropping stdin is the orderly shutdown: the read loop sees EOF and leaves.
    // Killing it would abandon a run mid-tool-call.
    if let Some(mut child) = agent.0.lock().unwrap().take() {
        let _ = child.write(b"{\"type\":\"shutdown\"}\n");
    }
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(Agent(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![
            agent_start,
            agent_write,
            agent_stop,
            roots_read,
            roots_write
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
