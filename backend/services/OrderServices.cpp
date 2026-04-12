#include "OrderServices.h"
#include "../core/TimeEngine.h"
#include "../core/PricingEngine.h"

Order OrderService::createOrder(int orderId, int canteenId, int itemId, int expectedTime) {
    Order order;
    order.orderId = orderId;
    order.canteenId = canteenId;
    order.itemId = itemId;
    order.expectedPrepTime = expectedTime;
    order.status = CREATED;
    return order;
}

void OrderService::acceptOrder(Order &order) {
    TimeEngine::markAccepted(order);
}

void OrderService::markReady(Order &order) {
    TimeEngine::markReady(order);
}

void OrderService::completeOrder(Order &order, int basePrice) {
    TimeEngine::markPickedUp(order);
    int delay = TimeEngine::calculatePrepDelay(order);
    order.finalPrice = PricingEngine::calculateFinalPrice(basePrice, delay);
}
