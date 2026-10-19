import { Logo } from "./Logo";

export const SUGGESTIONS = ["Show my positions", "Research NVDA", "Risks in Apple's latest 10-K"];

export function WelcomeHero({ onPick }: { onPick: (text: string) => void }) {
    return (
        <div className="empty-hero">
            <Logo />
            <h2>Broker Portfolio Agent</h2>
            <p>Ask about your positions, research a ticker, or place a trade.</p>
            <div className="suggestions">
                {SUGGESTIONS.map((text) => (
                    <button key={text} type="button" onClick={() => onPick(text)}>
                        {text}
                    </button>
                ))}
            </div>
        </div>
    );
}

export function LoadingHero() {
    return (
        <div className="loading-hero">
            <Logo />
            <h2>Loading...</h2>
        </div>
    );
}
