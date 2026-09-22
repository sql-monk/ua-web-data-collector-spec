import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClientProvider } from '@tanstack/react-query';
import { createBrowserRouter } from 'react-router';
import { RouterProvider } from 'react-router/dom';

import { queryClient } from '~/api/queryClient';
import { routes } from '~/routes/router';
import '~/components/app.css';

const container = document.getElementById('root');
if (!container) {
  throw new Error('Не знайдено кореневий елемент #root');
}

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={createBrowserRouter(routes)} />
    </QueryClientProvider>
  </StrictMode>,
);
