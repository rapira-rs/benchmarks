<?php

namespace App\Controller;

use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;
use Symfony\Component\Routing\Attribute\Route;

// Bench route: same body as the hello workload, so every leg and the k6
// checks share one contract. The skeleton's attribute scan of
// src/Controller/ picks it up; no routing config is touched. Content-Length
// is explicit because Symfony never adds it and nginx+fpm would chunk the
// response, changing the wire framing against the other legs.
final class BenchController
{
    #[Route('/', name: 'bench', methods: ['GET', 'HEAD'])]
    public function __invoke(Request $request): Response
    {
        $name = $request->query->get('name', 'anonymous');
        $body = "Hello from worker, {$name}!\n";

        return new Response($body, Response::HTTP_OK, [
            'Content-Type' => 'text/plain',
            'Content-Length' => (string) strlen($body),
        ]);
    }
}
