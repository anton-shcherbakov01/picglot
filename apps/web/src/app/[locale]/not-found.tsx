import Link from 'next/link';

export default function NotFound() {
  return (
    <div className="container-page grid min-h-[60vh] place-items-center py-20 text-center">
      <div>
        <p className="text-6xl font-bold text-accent">404</p>
        <h1 className="mt-4 text-2xl font-semibold">This page does not exist</h1>
        <p className="mt-2 text-muted">
          The link may be out of date, or the page may have been removed.
        </p>
        <Link href="/" className="btn-primary mt-6 inline-flex">
          Go to the home page
        </Link>
      </div>
    </div>
  );
}
