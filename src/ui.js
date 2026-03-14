export function showToast(message, type = 'success', duration = 3000) {
    let toast = document.getElementById('notification-toast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'notification-toast';
        document.body.appendChild(toast);
    }

    clearTimeout(toast.hideTimer);
    toast.textContent = message;

    const base = 'fixed bottom-20 left-1/2 transform -translate-x-1/2 px-6 py-3 rounded-full font-semibold text-sm shadow-lg transition-all duration-300 z-50';
    const colors = {
        success: 'bg-teal-600 text-white',
        error:   'bg-red-600 text-white',
        warning: 'bg-amber-500 text-white',
        info:    'bg-cyan-600 text-white',
    };
    toast.className = `${base} ${colors[type] || colors.info} opacity-100 translate-y-0`;

    toast.hideTimer = setTimeout(() => {
        toast.className = `${base} ${colors[type] || colors.info} opacity-0 translate-y-2`;
    }, duration);
}
