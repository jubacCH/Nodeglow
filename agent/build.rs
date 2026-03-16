fn main() {
    // Embed icon + version info into the Windows .exe
    #[cfg(target_os = "windows")]
    {
        let mut res = winresource::WindowsResource::new();
        res.set_icon("icon.ico");
        res.set("ProductName", "Nodeglow Agent");
        res.set("FileDescription", "Nodeglow Monitoring Agent");
        res.set("CompanyName", "Nodeglow");
        res.set("LegalCopyright", "MIT License");
        res.compile().expect("Failed to compile Windows resources");
    }
}
