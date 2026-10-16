import ConnectorSetup from "@/components/ConnectorSetup";

export default function ConnectorsPage() {
  return (
    <main className="min-h-screen bg-background text-foreground selection:bg-primary selection:text-black">
      <ConnectorSetup />
    </main>
  );
}
