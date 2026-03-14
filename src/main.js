import { getHealthChatContent, initHealthChat } from './features/health-chat.js';
import { showToast } from './ui.js';

function initDarkMode() {
    const btn = document.getElementById('theme-toggle');
    if (!btn) return;
    setTimeout(() => { document.body.classList.add('transition-colors', 'duration-300'); }, 100);
    btn.addEventListener('click', () => {
        const isDark = document.documentElement.classList.contains('dark');
        showToast(`${isDark ? 'Light' : 'Dark'} mode`, 'info', 1500);
    });
}

async function initApp() {
    try {
        initDarkMode();

        const mainContent = document.getElementById('main-content');
        if (!mainContent) throw new Error('Main content container not found');

        const html = await getHealthChatContent();
        mainContent.innerHTML = html;
        mainContent.classList.add('fade-in');
        setTimeout(() => mainContent.classList.remove('fade-in'), 400);

        initHealthChat();

        setTimeout(() => {
            showToast('Welcome to MediSense! Connect and start your session.', 'success', 4000);
        }, 600);

    } catch (error) {
        console.error('Init failed:', error);
        showToast('Failed to load interface. Please refresh.', 'error');
    }
}

document.addEventListener('DOMContentLoaded', initApp);
