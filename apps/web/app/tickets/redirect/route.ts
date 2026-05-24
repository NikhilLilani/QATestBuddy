import { NextResponse, type NextRequest } from 'next/server';

export async function GET(request: NextRequest) {
  const key = (request.nextUrl.searchParams.get('key') ?? '').trim().toUpperCase();
  if (!/^[A-Z][A-Z0-9_]+-\d+$/.test(key)) {
    return NextResponse.redirect(new URL('/tickets?error=invalid_key', request.url));
  }
  return NextResponse.redirect(new URL(`/tickets/${key}`, request.url));
}
