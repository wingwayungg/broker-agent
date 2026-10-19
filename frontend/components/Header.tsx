import { Suspense } from "react";
import { Logo } from "./Logo";
import { ResetButton } from "./ResetButton";

export function Header() {
    return (
        <header>
            <div className="title">
                <Logo />
                Broker Portfolio Agent
            </div>
            {/* Reads the URL, so on /thread/[id] it streams in after the
                prerendered shell rather than blocking it. */}
            <Suspense fallback={null}>
                <ResetButton />
            </Suspense>
        </header>
    );
}
