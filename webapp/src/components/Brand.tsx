export function Brand({ beta = true }: { beta?: boolean }) {
  return (
    <div className="logo" style={{ padding: 0 }}>
      <div className="logo-mark">J</div>
      <span className="logo-name">JoblyGo</span>
      {beta && <span className="logo-beta">β</span>}
    </div>
  );
}
