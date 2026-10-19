// 3-candlestick mark (irregular heights, mixed hollow/solid bodies) in
// currentColor, so it follows --accent. Each wick is two segments, above and
// below the body, so it doesn't cut through a hollow body.
export function Logo() {
    return (
        <span className="logo">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" aria-hidden="true">
                <line x1="6" y1="13" x2="6" y2="14" />
                <line x1="6" y1="18" x2="6" y2="20" />
                <rect x="4" y="14" width="4" height="4" fill="currentColor" stroke="none" />
                <line x1="12" y1="3" x2="12" y2="6" />
                <line x1="12" y1="13" x2="12" y2="17" />
                <rect x="10" y="6" width="4" height="7" />
                <line x1="18" y1="8" x2="18" y2="10" />
                <line x1="18" y1="16" x2="18" y2="19" />
                <rect x="16" y="10" width="4" height="6" fill="currentColor" stroke="none" />
            </svg>
        </span>
    );
}
