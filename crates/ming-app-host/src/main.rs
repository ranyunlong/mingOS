#[cfg(feature = "engine")]
fn main() {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.first().is_some_and(|v| v == "__worker") {
        let result = if args.len() == 1 {
            ming_app_host::engine::worker()
        } else {
            Err(anyhow::anyhow!("INVALID_WORKER_ARGUMENTS"))
        };
        if let Err(e) = result {
            ming_app_host::engine::report_worker_error(&e);
            std::process::exit(1);
        }
        return;
    }
    #[cfg(feature="desktop")]
    if args.first().is_some_and(|v|v=="desktop") {
        match ming_app_host::ming_desktop::run(args.into_iter().skip(1)) {
            Ok(outcome)=>match serde_json::to_string_pretty(&outcome) {Ok(text)=>println!("{text}"),Err(e)=>{eprintln!("OUTPUT_FAILED: {e}");std::process::exit(1);}},
            Err(e)=>{eprintln!("{e:#}");std::process::exit(1);}
        }
        return;
    }
    let result = ming_app_host::cli::parse(args).and_then(|options| {
        let snapshot = options.snapshot.clone();
        let mut outcome = if options.preview {
            #[cfg(feature = "desktop")]
            {
                ming_app_host::desktop::view(options)
            }
            #[cfg(not(feature = "desktop"))]
            {
                Err(anyhow::anyhow!("DESKTOP_FEATURE_REQUIRED"))
            }
        } else {
            ming_app_host::supervisor::run(options)
        }?;
        if let Some(path) = snapshot {
            let frame = outcome
                .rendered_frame
                .as_ref()
                .ok_or_else(|| anyhow::anyhow!("SNAPSHOT_NO_FRAME"))?;
            ming_app_host::graphics::write_png(&path, frame)?;
        }
        outcome.refresh_gpu_stats()?;
        Ok(outcome)
    });
    match result {
        Ok(outcome) => match serde_json::to_string_pretty(&outcome) {
            Ok(text) => println!("{text}"),
            Err(e) => {
                eprintln!("OUTPUT_FAILED: {e}");
                std::process::exit(1);
            }
        },
        Err(e) => {
            eprintln!("{e:#}");
            std::process::exit(1);
        }
    }
}
#[cfg(not(feature = "engine"))]
fn main() {
    eprintln!("ENGINE_FEATURE_REQUIRED");
    std::process::exit(1);
}
