#include "OrderAPI.h"
#include "../services/OrderServices.h"

Order OrderAPI::createOrderAPI(
    int orderId,
    int canteenId,
    int itemId,
    int expectedPrepTime
) {
    return OrderService::createOrder(
        orderId,
        canteenId,
        itemId,
        expectedPrepTime
    );
}

void OrderAPI::acceptOrderAPI(Order &order) {
    OrderService::acceptOrder(order);
}

void OrderAPI::markReadyAPI(Order &order) {
    OrderService::markReady(order);
}

void OrderAPI::pickupOrderAPI(
    Order &order,
    int basePrice
) {
    OrderService::completeOrder(order, basePrice);
}
