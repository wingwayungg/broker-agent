import { LoadingHero } from "@/components/Heroes";

// Streamed at once while the thread is read from the API.
export default function Loading() {
    return (
        <div id="log" className="loading">
            <div id="log-inner">
                <LoadingHero />
            </div>
        </div>
    );
}
