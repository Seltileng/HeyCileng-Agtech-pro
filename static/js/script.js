// Lets the CSS know JavaScript is running, so it can swap plain file
// inputs for the nicer upload boxes below.
document.documentElement.classList.add("js");

// ---------- Light and dark theme ----------
// base.html has already applied the saved theme before the page was drawn.
// These buttons switch it and save the choice for every page and visit.
const THEME_KEY = "heycileng-theme";
const themeColour = document.querySelector('meta[name="theme-color"]');

function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    const next = theme === "dark" ? "light" : "dark";
    document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
        button.setAttribute("aria-label", "Switch to " + next + " mode");
        button.setAttribute("aria-pressed", theme === "dark");
    });
    if (themeColour) {
        themeColour.setAttribute("content", theme === "dark" ? "#142a1d" : "#1d3a2a");
    }
}

applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light");

document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
    button.addEventListener("click", function () {
        const theme = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
        applyTheme(theme);
        try {
            localStorage.setItem(THEME_KEY, theme);
        } catch (error) {
            // Private browsing can block storage; the theme still changes for this page.
        }
    });
});

// ---------- Public pages: hamburger menu below 820px ----------
const menuButton = document.querySelector(".menu-button");
const siteNav = document.querySelector(".site-nav");

if (menuButton && siteNav) {
    const setMenuOpen = function (isOpen) {
        siteNav.classList.toggle("open", isOpen);
        menuButton.setAttribute("aria-expanded", isOpen);
        menuButton.setAttribute("aria-label", isOpen ? "Close menu" : "Open menu");
    };

    menuButton.addEventListener("click", function () {
        setMenuOpen(!siteNav.classList.contains("open"));
    });

    // Close the menu after a link is tapped.
    siteNav.addEventListener("click", function (event) {
        if (event.target.closest("a")) {
            setMenuOpen(false);
        }
    });

    // Close the menu when the Escape key is pressed, and put focus back on the button.
    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && siteNav.classList.contains("open")) {
            setMenuOpen(false);
            menuButton.focus();
        }
    });

    // Close the menu when someone taps anywhere outside it.
    document.addEventListener("click", function (event) {
        if (siteNav.classList.contains("open") && !event.target.closest(".site-header")) {
            setMenuOpen(false);
        }
    });

    // 821px matches the @media (max-width: 820px) rule in style.css.
    window.matchMedia("(min-width: 821px)").addEventListener("change", function (event) {
        if (event.matches) {
            setMenuOpen(false);
        }
    });
}

// ---------- Logged-in pages: slide-out sidebar below 960px ----------
const appShell = document.querySelector(".app-shell");
const sidebarOpenButton = document.querySelector("[data-sidebar-open]");

if (appShell && sidebarOpenButton) {
    const sidebar = document.getElementById("app-sidebar");

    const setSidebarOpen = function (isOpen) {
        appShell.classList.toggle("sidebar-open", isOpen);
        sidebarOpenButton.setAttribute("aria-expanded", isOpen);
        if (isOpen) {
            // Move focus into the menu so keyboard users land on its first link.
            const first = sidebar.querySelector("a, button");
            if (first) {
                first.focus();
            }
        }
    };

    sidebarOpenButton.addEventListener("click", function () {
        setSidebarOpen(true);
    });

    document.querySelectorAll("[data-sidebar-close]").forEach(function (closer) {
        closer.addEventListener("click", function () {
            setSidebarOpen(false);
            sidebarOpenButton.focus();
        });
    });

    // Tapping a link closes the menu (it also leaves the page, or jumps within it).
    sidebar.addEventListener("click", function (event) {
        if (event.target.closest("a")) {
            setSidebarOpen(false);
        }
    });

    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && appShell.classList.contains("sidebar-open")) {
            setSidebarOpen(false);
            sidebarOpenButton.focus();
        }
    });

    // 961px matches the @media (max-width: 960px) rule in style.css.
    window.matchMedia("(min-width: 961px)").addEventListener("change", function (event) {
        if (event.matches) {
            setSidebarOpen(false);
        }
    });
}

// Show/Hide buttons on password fields. The buttons are hidden in the HTML,
// so they only appear when this script is running and can make them work.
document.querySelectorAll("[data-password-toggle]").forEach(function (button) {
    const input = document.getElementById(button.dataset.passwordToggle);
    button.hidden = false;
    button.setAttribute("aria-label", "Show password");

    button.addEventListener("click", function () {
        const showing = input.type === "text";
        input.type = showing ? "password" : "text";
        button.textContent = showing ? "Show" : "Hide";
        button.setAttribute("aria-pressed", !showing);
        button.setAttribute("aria-label", showing ? "Show password" : "Hide password");
    });
});

// "Confirm password" fields: the browser will not submit the form until the
// two passwords match. The server checks this again, so nothing relies on it.
document.querySelectorAll("[data-must-match]").forEach(function (confirmInput) {
    const original = document.getElementById(confirmInput.dataset.mustMatch);

    function check() {
        const matches = confirmInput.value === "" || confirmInput.value === original.value;
        confirmInput.setCustomValidity(matches ? "" : "The passwords do not match.");
    }

    confirmInput.addEventListener("input", check);
    original.addEventListener("input", check);
});

// ---------- Photo and video uploads on the listing form ----------
// Shows a preview of each chosen file with a Remove button. These checks are
// only for convenience: the server checks every file again before saving.
const ALLOWED_TYPES = {
    image: { extensions: ["jpg", "jpeg", "png", "webp"], label: "JPG, PNG or WEBP photo" },
    video: { extensions: ["mp4", "webm"], label: "MP4 or WEBM video" },
};

function formatSize(bytes) {
    return bytes >= 1024 * 1024 ? (bytes / (1024 * 1024)).toFixed(1) + " MB" : Math.ceil(bytes / 1024) + " KB";
}

document.querySelectorAll("[data-media-input]").forEach(function (input) {
    const kind = input.dataset.mediaInput;
    const form = input.form;
    const maxSize = Number(input.dataset.maxSize);
    const previews = form.querySelector('[data-media-previews="' + input.id + '"]');
    const message = form.querySelector('[data-media-message="' + input.id + '"]');
    // Browsers without DataTransfer cannot remove single files from an input.
    const canEdit = typeof DataTransfer === "function";
    let chosen = [];

    // How many more files fit, counting current photos not ticked for removal.
    function spaceLeft() {
        if (kind === "video") {
            return 1;
        }
        const kept = form.querySelectorAll('.media-existing[data-kind="image"] input[name="remove_media"]:not(:checked)').length;
        return Number(input.dataset.maxFiles) - kept;
    }

    function syncInput() {
        if (!canEdit) {
            return;
        }
        const transfer = new DataTransfer();
        chosen.forEach(function (item) { transfer.items.add(item.file); });
        input.files = transfer.files;
    }

    // Frees the memory a preview uses. Only done when its file is dropped.
    function forget(item) {
        URL.revokeObjectURL(item.url);
    }

    function render() {
        previews.innerHTML = "";

        chosen.forEach(function (item, index) {
            const li = document.createElement("li");
            li.className = "media-item media-new";
            const media = document.createElement(kind === "image" ? "img" : "video");
            media.src = item.url;
            if (kind === "image") {
                media.alt = "Preview of " + item.file.name;
            } else {
                media.controls = true;
                media.preload = "metadata";
            }
            const caption = document.createElement("p");
            caption.className = "media-caption";
            caption.textContent = item.file.name + " · " + formatSize(item.file.size);
            li.append(media, caption);

            if (canEdit) {
                const remove = document.createElement("button");
                remove.type = "button";
                remove.className = "media-remove";
                remove.textContent = "Remove";
                remove.setAttribute("aria-label", "Remove " + item.file.name);
                remove.addEventListener("click", function () {
                    forget(item);
                    chosen.splice(index, 1);
                    syncInput();
                    render();
                    message.textContent = item.file.name + " removed.";
                });
                li.append(remove);
            }
            previews.append(li);
        });
    }

    input.addEventListener("change", function () {
        const problems = [];
        // Picking files again adds to the earlier choice instead of replacing it.
        const incoming = Array.from(input.files);
        if (kind === "video" || !canEdit) {
            chosen.forEach(forget);
            chosen = [];
        }

        incoming.forEach(function (file) {
            const extension = file.name.split(".").pop().toLowerCase();
            if (!ALLOWED_TYPES[kind].extensions.includes(extension)) {
                problems.push(file.name + " is not a " + ALLOWED_TYPES[kind].label + ".");
            } else if (file.size > maxSize) {
                problems.push(file.name + " is larger than " + formatSize(maxSize) + ".");
            } else if (chosen.length >= spaceLeft()) {
                problems.push(file.name + " was not added: the limit is " + input.dataset.maxFiles + (kind === "image" ? " photos" : " video") + ".");
            } else {
                chosen.push({ file: file, url: URL.createObjectURL(file) });
            }
        });

        syncInput();
        render();
        message.textContent = problems.join(" ");
        message.classList.toggle("media-message-error", problems.length > 0);
    });
});

// Shows "Will be removed" on current photos and videos ticked for removal.
document.querySelectorAll('.media-existing input[name="remove_media"]').forEach(function (box) {
    box.addEventListener("change", function () {
        box.closest(".media-existing").classList.toggle("media-removing", box.checked);
    });
    box.closest(".media-existing").classList.toggle("media-removing", box.checked);
});

// Listing gallery: one photo or video at a time in the stage, picked with the
// thumbnails or the Previous / Next buttons.
document.querySelectorAll("[data-gallery]").forEach(function (gallery) {
    const items = Array.from(gallery.querySelectorAll("[data-gallery-item]"));
    const thumbs = Array.from(gallery.querySelectorAll("[data-gallery-thumb]"));
    const previous = gallery.querySelector("[data-gallery-prev]");
    const next = gallery.querySelector("[data-gallery-next]");
    const count = gallery.querySelector("[data-gallery-count]");
    let current = 0;

    function show(index) {
        // Pause a playing video before moving away from it.
        const video = items[current].querySelector("video");
        if (video) {
            video.pause();
        }
        current = index;
        items.forEach(function (item, i) { item.hidden = i !== index; });
        thumbs.forEach(function (thumb, i) {
            if (i === index) {
                thumb.setAttribute("aria-current", "true");
            } else {
                thumb.removeAttribute("aria-current");
            }
        });
        if (count) {
            count.textContent = (index + 1) + " / " + items.length;
            previous.disabled = index === 0;
            next.disabled = index === items.length - 1;
        }
    }

    thumbs.forEach(function (thumb) {
        thumb.addEventListener("click", function () { show(Number(thumb.dataset.galleryThumb)); });
    });
    if (previous) {
        previous.addEventListener("click", function () { show(Math.max(0, current - 1)); });
        next.addEventListener("click", function () { show(Math.min(items.length - 1, current + 1)); });
    }
});

// "Add New Listing" links scroll to the form and put the cursor in its first
// field, so the seller can start typing straight away. The scrolling is done
// here rather than left to the browser, because browsers do not scroll again
// when the address already ends in #add-listing (as it does after a form error).
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

document.querySelectorAll("[data-focus-target]").forEach(function (link) {
    link.addEventListener("click", function (event) {
        const target = document.querySelector(link.getAttribute("href"));
        const field = document.getElementById(link.dataset.focusTarget);
        if (!target) {
            return;
        }
        event.preventDefault();
        target.scrollIntoView({ behavior: reduceMotion.matches ? "auto" : "smooth", block: "start" });
        if (!field) {
            return;
        }
        // Focusing while the page is still scrolling can stop the scroll
        // part-way, so wait until it has finished. "scrollend" says when;
        // the timer covers browsers without it, or when no scroll was needed.
        let done = false;
        function focusField() {
            if (!done) {
                done = true;
                field.focus({ preventScroll: true });
            }
        }
        window.addEventListener("scrollend", focusField, { once: true });
        setTimeout(focusField, reduceMotion.matches ? 50 : 1200);
    });
});

// Asks "are you sure?" before submitting forms that have a data-confirm
// message, using the dialog in base.html. Browsers without <dialog> support
// get their own plain confirm box instead.
const confirmDialog = document.getElementById("confirm-dialog");

document.addEventListener("submit", function (event) {
    const form = event.target;
    const message = form.dataset.confirm;
    if (!message || form.dataset.confirmed === "yes") {
        return;
    }
    event.preventDefault();

    if (!confirmDialog || typeof confirmDialog.showModal !== "function") {
        if (window.confirm(message)) {
            form.dataset.confirmed = "yes";
            form.requestSubmit();
        }
        return;
    }

    confirmDialog.querySelector("[data-confirm-message]").textContent = message;
    confirmDialog.returnValue = "";
    confirmDialog.addEventListener("close", function () {
        if (confirmDialog.returnValue === "confirm") {
            form.dataset.confirmed = "yes";
            form.requestSubmit();
        }
    }, { once: true });
    confirmDialog.showModal();
    // Cancel is focused first, so pressing Enter by accident deletes nothing.
    confirmDialog.querySelector('[value="cancel"]').focus();
});

// Toasts: messages in the bottom-right corner. The x button closes one, and
// success messages also fade away by themselves after 8 seconds.
document.querySelectorAll(".toast").forEach(function (toast) {
    const close = toast.querySelector(".toast-close");
    close.hidden = false;
    close.addEventListener("click", function () { toast.remove(); });
    if (!toast.classList.contains("alert-error")) {
        setTimeout(function () { toast.remove(); }, 8000);
    }
});
