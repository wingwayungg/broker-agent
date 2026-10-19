import { Logo } from "./Logo";

export function Header({ onReset, resetDisabled = false }: { onReset?: () => void; resetDisabled?: boolean }) {
    return (
        <header>
            <div className="title">
                <Logo />
                Broker Portfolio Agent
            </div>
            {onReset && (
                <button id="reset" type="button" onClick={onReset} disabled={resetDisabled}>
                    New conversation
                </button>
            )}
        </header>
    );
}
