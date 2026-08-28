// Theme toggle, shared by every voicechat2 screen.
//
// Markup each page includes (in the header, alongside the page title):
//   <button id="theme-toggle" class="theme-toggle" type="button" aria-label="Toggle theme"></button>
//
// Behavior: reads/writes localStorage, toggles data-theme on <html>. Absence
// of a stored choice means "follow the OS" — theme.css already handles that
// via prefers-color-scheme, so this script only sets data-theme once the
// user has explicitly chosen a theme.

const THEME_STORAGE_KEY = "vc2-theme";

function storedTheme() {
    const value = localStorage.getItem(THEME_STORAGE_KEY);
    return value === "light" || value === "dark" ? value : null;
}

function currentTheme() {
    return storedTheme() ?? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}

function setTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem(THEME_STORAGE_KEY, theme);
    updateToggleButton();
}

function toggleTheme() {
    setTheme(currentTheme() === "dark" ? "light" : "dark");
}

function updateToggleButton() {
    const button = document.getElementById("theme-toggle");
    if (!button) return;
    const isDark = currentTheme() === "dark";
    button.textContent = isDark ? "☀️" : "🌙";
    button.setAttribute("aria-label", isDark ? "Switch to light theme" : "Switch to dark theme");
}

// Applied immediately (not on DOMContentLoaded) so a stored choice takes
// effect before first paint, avoiding a flash of the wrong theme.
const initialTheme = storedTheme();
if (initialTheme) {
    document.documentElement.setAttribute("data-theme", initialTheme);
}

document.addEventListener("DOMContentLoaded", () => {
    const button = document.getElementById("theme-toggle");
    if (button) button.addEventListener("click", toggleTheme);
    updateToggleButton();
});
