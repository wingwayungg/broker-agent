import { Composer } from "@/components/Composer";
import { Header } from "@/components/Header";
import { LoadingHero } from "@/components/Heroes";

// Streamed at once while the thread is read from the API.
export default function Loading() {
    return (
        <>
            <Header />
            <main>
                <div id="log" className="loading">
                    <div id="log-inner">
                        <LoadingHero />
                    </div>
                </div>
            </main>
            <Composer disabled />
        </>
    );
}
