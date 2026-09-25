import { ComponentFixture, TestBed } from '@angular/core/testing';

import { CartComponent } from '../app/cart/cart.component';
import { CartService } from '../app/cart/cart.service';
import { Product } from '../app/cart/product';
import { ProductListComponent } from '../app/products/product-list.component';

const apple: Product = { id: 'apple', name: 'Apple', price: 0.5 };
const pear: Product = { id: 'pear', name: 'Pear', price: 1.25 };

describe('hidden: CartService', () => {
  let cart: CartService;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    cart = TestBed.inject(CartService);
  });

  it('adds units to the same line', () => {
    cart.add(apple);
    cart.add(apple);
    cart.add(pear);
    expect(cart.items().length).toBe(2);
    expect(cart.items().find((i) => i.product.id === 'apple')?.quantity).toBe(2);
    expect(cart.count()).toBe(3);
    expect(cart.total()).toBeCloseTo(2.25, 5);
  });

  it('sets quantities and removes on <= 0', () => {
    cart.add(apple);
    cart.setQuantity('apple', 4);
    expect(cart.count()).toBe(4);
    cart.setQuantity('apple', 0);
    expect(cart.items().length).toBe(0);
  });

  it('removes and clears', () => {
    cart.add(apple);
    cart.add(pear);
    cart.remove('apple');
    expect(cart.items().map((i) => i.product.id)).toEqual(['pear']);
    cart.clear();
    expect(cart.count()).toBe(0);
    expect(cart.total()).toBe(0);
  });
});

describe('hidden: ProductListComponent + CartComponent', () => {
  let list: ComponentFixture<ProductListComponent>;
  let view: ComponentFixture<CartComponent>;

  const q = (f: ComponentFixture<unknown>, id: string) =>
    (f.nativeElement as HTMLElement).querySelector<HTMLElement>(`[data-testid="${id}"]`);
  const all = (f: ComponentFixture<unknown>, id: string) => [
    ...(f.nativeElement as HTMLElement).querySelectorAll<HTMLElement>(`[data-testid="${id}"]`),
  ];
  const refresh = () => {
    list.detectChanges();
    view.detectChanges();
  };

  beforeEach(() => {
    TestBed.configureTestingModule({ imports: [ProductListComponent, CartComponent] });
    list = TestBed.createComponent(ProductListComponent);
    list.componentRef.setInput('products', [apple, pear]);
    view = TestBed.createComponent(CartComponent);
    refresh();
  });

  it('lists products', () => {
    expect(all(list, 'product').length).toBe(2);
    expect(q(list, 'product')?.textContent).toContain('Apple');
  });

  it('shows the empty state', () => {
    expect(q(view, 'cart-empty')).not.toBeNull();
    expect(all(view, 'cart-line').length).toBe(0);
  });

  it('adds from the list and shows lines, count and a 2-decimal total', () => {
    q(list, 'add-apple')?.click();
    q(list, 'add-apple')?.click();
    q(list, 'add-pear')?.click();
    refresh();
    expect(q(view, 'cart-empty')).toBeNull();
    expect(all(view, 'cart-line').length).toBe(2);
    expect(q(view, 'cart-count')?.textContent?.trim()).toBe('3');
    expect(q(view, 'cart-total')?.textContent?.trim()).toBe('2.25');
  });

  it('removes a line from the cart view', () => {
    q(list, 'add-pear')?.click();
    q(list, 'add-pear')?.click();
    refresh();
    expect(q(view, 'cart-total')?.textContent?.trim()).toBe('2.50');
    q(view, 'remove-pear')?.click();
    refresh();
    expect(q(view, 'cart-empty')).not.toBeNull();
  });
});
